import os
from pathlib import Path
from dotenv import load_dotenv
from neo4j import GraphDatabase
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

    def close(self):
        self.driver.close()
