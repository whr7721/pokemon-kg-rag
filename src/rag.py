import os
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl
from neo4j_graphrag.retrievers import VectorCypherRetriever
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.generation import GraphRAG, RagTemplate
from neo4j_graphrag.types import RetrieverResultItem
from embedder import BgeM3Embedder

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

RETRIEVAL_QUERY = """
MATCH (node)-[:DESCRIBES]->(e)
RETURN node.text AS text,
       node.kind AS kind,
       node.entity_id AS entity_id,
       labels(e)[0] AS entity_label,
       coalesce(e.name_zh, e.name, '') AS entity_name,
       score AS score
"""

SUBGRAPH_QUERIES = {
    "Pokemon": """
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[r:HAS_TYPE|HAS_ABILITY|BELONGS_TO_EGG_GROUP|BELONGS_TO_GENERATION|EVOLVES_TO]->(m)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS sl, coalesce(n.name_zh, n.name, '') AS sn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.name, toString(m.num), '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (m)-[r:EVOLVES_TO]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS sl, coalesce(n.name_zh, n.name, '') AS sn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.name, toString(m.num), '') AS tn
    """,
    "Move": """
        MATCH (n:Move {name_zh: $eid})
        OPTIONAL MATCH (m)-[r:LEARNS_MOVE]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS sl, coalesce(m.name_zh, m.name, '') AS sn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, n.name, '') AS tn
        LIMIT 15
    """,
    "Ability": """
        MATCH (n:Ability {name_zh: $eid})
        OPTIONAL MATCH (m)-[r:HAS_ABILITY]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS sl, coalesce(m.name_zh, m.name, '') AS sn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, n.name, '') AS tn
        LIMIT 15
    """,
}

def record_formatter(record):
    return RetrieverResultItem(
        content=record["text"],
        metadata={
            "kind": record.get("kind"),
            "entity_id": record.get("entity_id"),
            "entity_label": record.get("entity_label"),
            "entity_name": record.get("entity_name"),
            "score": record.get("score"),
        },
    )

def get_driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
        notifications_disabled_classifications=["DEPRECATION"],
    )

class PokemonGraphRAG:
    def __init__(self):
        self.driver = get_driver()
        self.db = os.getenv("NEO4J_DB")
        self.embedder = BgeM3Embedder()
        self.retriever = VectorCypherRetriever(
            self.driver,
            "chunk_embedding",
            RETRIEVAL_QUERY,
            self.embedder,
            result_formatter=record_formatter,
            neo4j_database=self.db,
        )
        self.llm = OpenAILLM(
            model_name=os.getenv("LLM_MODEL", "tju-llm"),
            base_url=os.getenv("LLM_ENDPOINT"),
            api_key=os.getenv("LLM_TOKEN"),
        )
        self.rag = GraphRAG(
            retriever=self.retriever,
            llm=self.llm,
            prompt_template=RagTemplate(
                template=(
                    "你是一个宝可梦知识助手。请只依据下面的上下文回答问题；"
                    "如果上下文不足以回答，请直接说“资料不足”。\n\n"
                    "示例:\n{examples}\n\n"
                    "上下文:\n{context}\n\n"
                    "问题: {query_text}\n\n"
                    "回答:"
                ),
                expected_inputs=["context", "query_text", "examples"],
                system_instructions="你只根据给定上下文回答宝可梦相关问题，不要编造事实。",
            ),
        )

    def retrieve(self, question, top_k=5):
        result = self.retriever.search(query_text=question, top_k=top_k)
        return [
            {"text": item.content, "metadata": item.metadata}
            for item in result.items
        ]

    def ask(self, question, top_k=5):
        result = self.rag.search(
            query_text=question,
            retriever_config={"top_k": top_k},
            return_context=True,
        )
        evidence = (
            [{"text": item.content, "metadata": item.metadata} for item in result.retriever_result.items]
            if result.retriever_result else []
        )
        return {"answer": result.answer, "evidence": evidence}

    def subgraph(self, items, limit=3):
        nodes = {}
        edges = []
        for item in items[:limit]:
            label = item["metadata"]["entity_label"]
            eid = item["metadata"]["entity_id"]
            query = SUBGRAPH_QUERIES.get(label)
            if not query:
                continue
            records, _, _ = self.driver.execute_query(
                query, {"eid": eid},
                database_=self.db,
                routing_=RoutingControl.READ,
            )
            for rec in records:
                sn, sl, rel, tn, tl = rec["sn"], rec["sl"], rec["rel"], rec["tn"], rec["tl"]
                if not tn:
                    continue
                sid = f"{sl}:{sn}"
                tid = f"{tl}:{tn}"
                nodes[sid] = {"id": sid, "label": sn, "group": sl}
                nodes[tid] = {"id": tid, "label": tn, "group": tl}
                edges.append({"from": sid, "to": tid, "label": rel})
        seen = set()
        unique_edges = []
        for e in edges:
            key = (e["from"], e["to"], e["label"])
            if key not in seen:
                seen.add(key)
                unique_edges.append(e)
        return {"nodes": list(nodes.values()), "edges": unique_edges}

    def close(self):
        self.driver.close()
