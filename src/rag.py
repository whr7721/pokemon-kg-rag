# -*- coding: utf-8 -*-
"""GraphRAG 检索与生成（阶段一最终合并版）。

链路：问题 -> 本地/API BGE-M3 -> Neo4j 向量召回 Chunk -> 经 DESCRIBES 回到实体
      -> 提取前若干实体的图谱邻域事实 -> 与文本块共同拼入 Prompt -> 学校 Qwen 生成。

适用 Schema（增强版，见 src/build_engine.py）：
    Pokemon -[:HAS_FORM]-> Form -[:HAS_TYPE|HAS_ABILITY|IN_EGG_GROUP|LEARNS]-> ...
    Type -[:HITS_TYPE]-> Type；Pokemon -[:EVOLVES_TO]-> Pokemon；叙事边可选。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl
from neo4j_graphrag.generation import RagTemplate
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.retrievers import VectorCypherRetriever
from neo4j_graphrag.types import RetrieverResultItem

from embedder import make_embedder

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

VECTOR_INDEX = os.getenv("VECTOR_INDEX") or "embedding_Chunk"

RETRIEVAL_QUERY = """
MATCH (node)-[:DESCRIBES]->(e)
OPTIONAL MATCH (par:Pokemon)-[:HAS_FORM]->(e)
WITH node, score,
     CASE WHEN par IS NOT NULL THEN par ELSE e END AS ent,
     CASE WHEN par IS NOT NULL THEN 'Pokemon' ELSE labels(e)[0] END AS elab
RETURN node.text AS text,
       coalesce(node.entity_type, node.kind, '') AS kind,
       CASE WHEN elab = 'Pokemon' THEN ent.pokedex_id
            WHEN elab IN ['Move', 'Ability'] THEN ent.id
            ELSE coalesce(ent.pokedex_id, ent.id, '') END AS entity_id,
       elab AS entity_label,
       coalesce(ent.name_zh, ent.name, ent.form_name, toString(ent.num), '') AS entity_name,
       score AS score
"""

SUBGRAPH_QUERIES = {
    "Pokemon": """
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[:HAS_FORM]->(:Form)-[r:HAS_TYPE|HAS_ABILITY|IN_EGG_GROUP]->(m)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS sl, coalesce(n.name_zh, '') AS sn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.id, '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (m)-[r:EVOLVES_TO]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS sl, coalesce(n.name_zh, '') AS sn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.id, '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[r:EVOLVES_TO]->(m)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS sl, coalesce(n.name_zh, '') AS sn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.id, '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[r:PREDATES_ON|RIVAL_OF|ALLIED_WITH|COMPETES_WITH|COMMENSAL_OF|MENTOR_OF|SYMBIOTIC_WITH]->(m:Pokemon)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS sl, coalesce(n.name_zh, '') AS sn,
               type(r) AS rel,
               labels(m)[0] AS tl, coalesce(m.name_zh, '') AS tn
    """,
    "Move": """
        MATCH (n:Move {id: $eid})
        OPTIONAL MATCH (m:Pokemon)-[:HAS_FORM]->(:Form)-[r:LEARNS]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS sl, coalesce(m.name_zh, '') AS sn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, '') AS tn
        LIMIT 15
    """,
    "Ability": """
        MATCH (n:Ability {id: $eid})
        OPTIONAL MATCH (m:Pokemon)-[:HAS_FORM]->(:Form)-[r:HAS_ABILITY]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS sl, coalesce(m.name_zh, '') AS sn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, '') AS tn
        LIMIT 15
    """,
}

ENTITY_FACT_QUERIES = {
    "Pokemon": """
        MATCH (p:Pokemon {pokedex_id: $eid})
        CALL (p) {
            OPTIONAL MATCH (p)-[:HAS_FORM]->(:Form)-[:HAS_TYPE]->(t:Type)
            OPTIONAL MATCH (t)-[h:HITS_TYPE]-(tgt:Type)
            RETURN [x IN collect(DISTINCT t.name_zh) WHERE x IS NOT NULL] AS types,
                   [x IN collect(DISTINCT {from: startNode(h).name_zh, to: endNode(h).name_zh, mult: h.multiplier})
                    WHERE x.from IS NOT NULL AND x.to IS NOT NULL] AS type_chart
        }
        CALL (p) {
            OPTIONAL MATCH (p)-[:HAS_FORM]->(:Form)-[ha:HAS_ABILITY]->(a:Ability)
            RETURN [x IN collect(DISTINCT {name: a.name_zh, hidden: ha.hidden}) WHERE x.name IS NOT NULL] AS abilities
        }
        CALL (p) {
            OPTIONAL MATCH (p)-[:HAS_FORM]->(:Form)-[:IN_EGG_GROUP]->(e:EggGroup)
            RETURN [x IN collect(DISTINCT e.name_zh) WHERE x IS NOT NULL] AS egg_groups
        }
        CALL (p) {
            OPTIONAL MATCH (p)-[:BELONGS_TO_GENERATION]->(g:Generation)
            RETURN g.num AS generation
        }
        CALL (p) {
            OPTIONAL MATCH (prev:Pokemon)-[r_prev:EVOLVES_TO]->(p)
            OPTIONAL MATCH (p)-[r_next:EVOLVES_TO]->(nxt:Pokemon)
            OPTIONAL MATCH (p)-[:EVOLVES_TO]->(:Pokemon)-[r_final:EVOLVES_TO]->(final:Pokemon)
            RETURN [x IN collect(DISTINCT {from: prev.name_zh, condition: r_prev.condition}) WHERE x.from IS NOT NULL] AS evolves_from,
                   [x IN collect(DISTINCT {to: nxt.name_zh, condition: r_next.condition}) WHERE x.to IS NOT NULL] AS evolves_to,
                   [x IN collect(DISTINCT {final: final.name_zh, condition: r_final.condition}) WHERE x.final IS NOT NULL] AS final_evolution
        }
        CALL (p) {
            OPTIONAL MATCH (p)-[r_narr:PREDATES_ON|RIVAL_OF|ALLIED_WITH|COMPETES_WITH|COMMENSAL_OF|MENTOR_OF|SYMBIOTIC_WITH]->(other:Pokemon)
            RETURN [x IN collect(DISTINCT {rel: type(r_narr), other: other.name_zh, evidence: r_narr.evidence})
                    WHERE x.other IS NOT NULL] AS narrative
        }
        RETURN p.name_zh AS name, p.pokedex_id AS id, p.category AS category, generation,
               types, type_chart, abilities, egg_groups, evolves_from, evolves_to, final_evolution, narrative
    """,
    "Move": """
        MATCH (m:Move {id: $eid})
        OPTIONAL MATCH (p:Pokemon)-[:HAS_FORM]->(:Form)-[:LEARNS]->(m)
        RETURN m.name_zh AS name, m.type AS type, m.category AS category,
               m.power AS power, m.accuracy AS accuracy, m.pp AS pp, m.description AS description,
               [x IN collect(DISTINCT p.name_zh) WHERE x IS NOT NULL][..8] AS learned_by
    """,
    "Ability": """
        MATCH (a:Ability {id: $eid})
        OPTIONAL MATCH (p:Pokemon)-[:HAS_FORM]->(:Form)-[:HAS_ABILITY]->(a)
        RETURN a.name_zh AS name, a.description AS description, a.generation AS generation,
               [x IN collect(DISTINCT p.name_zh) WHERE x IS NOT NULL][..8] AS pokemon_list
    """,
}

NARRATIVE_CN = {
    "RIVAL_OF": "宿敌", "PREDATES_ON": "捕食", "COMPETES_WITH": "竞争",
    "ALLIED_WITH": "结盟", "COMMENSAL_OF": "共生", "MENTOR_OF": "师徒",
    "SYMBIOTIC_WITH": "互利共生",
}


def record_formatter(record):
    name = record.get("entity_name")
    prefix = f"[{name}] " if name else ""
    return RetrieverResultItem(
        content=prefix + record["text"],
        metadata={
            "kind": record.get("kind"),
            "entity_id": record.get("entity_id"),
            "entity_label": record.get("entity_label"),
            "entity_name": name,
            "score": record.get("score"),
        },
    )


def dedupe_by_entity(items, limit=None):
    """同一实体多个文本块只保留召回分最高的一条，保持召回顺序。"""
    seen, out = set(), []
    for item in items:
        md = item.metadata
        key = (md.get("entity_label"), md.get("entity_id"))
        if not all(key):
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if limit and len(out) >= limit:
            break
    return out


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
        self.embedder = make_embedder()
        self.retriever = VectorCypherRetriever(
            self.driver,
            VECTOR_INDEX,
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
        self.prompt_template = RagTemplate(
            template=(
                "你是一个宝可梦知识助手。请优先参考上下文中的【图谱结构化事实】和【检索文本块】准确回答问题。\n"
                "如果上下文完全不足以回答，请直接说“资料不足”。\n\n"
                "示例:\n{examples}\n\n"
                "上下文:\n{context}\n\n"
                "问题: {query_text}\n\n"
                "回答:"
            ),
            expected_inputs=["context", "query_text", "examples"],
            system_instructions="你只根据给定上下文回答宝可梦相关问题，不要编造事实。",
        )

    def retrieve(self, question, top_k=5):
        result = self.retriever.search(query_text=question, top_k=top_k)
        items = dedupe_by_entity(result.items)
        return [
            {"text": item.content, "metadata": item.metadata}
            for item in items
        ]

    def get_entity_facts(self, items, limit=3):
        seen = set()
        entities = []
        for it in items:
            meta = it.get("metadata") or {}
            pair = (meta.get("entity_label"), meta.get("entity_id"))
            if pair[0] and pair[1] and pair not in seen:
                seen.add(pair)
                entities.append(pair)
                if len(entities) >= limit:
                    break

        facts = []
        for label, eid in entities:
            query = ENTITY_FACT_QUERIES.get(label)
            if not query:
                continue
            records, _, _ = self.driver.execute_query(
                query, {"eid": eid}, database_=self.db, routing_=RoutingControl.READ
            )
            if records:
                facts.append({"label": label, "data": records[0].data()})
        return facts

    def format_fact(self, fact):
        rec, label = fact.get("data") or {}, fact.get("label")
        if not rec:
            return ""
        if label == "Pokemon":
            types_str = " / ".join(rec.get("types") or [])
            lines = [f"【宝可梦】{rec.get('name')}(编号:{rec.get('id')}) 属性:{types_str} 分类:{rec.get('category','')} 第{rec.get('generation','')}世代"]
            if rec.get("abilities"):
                lines.append("  * 特性: " + "、".join(
                    f"{a['name']}(隐藏)" if str(a.get("hidden")) == "True" else a['name']
                    for a in rec["abilities"]))
            if rec.get("egg_groups"):
                lines.append("  * 蛋群: " + "、".join(rec["egg_groups"]))
            if rec.get("evolves_from"):
                lines.append("  * 前置进化: " + "；".join(
                    f"{x['from']}({x.get('condition') or '常规'})" for x in rec["evolves_from"]))
            if rec.get("evolves_to"):
                lines.append("  * 后续进化: " + "；".join(
                    f"{x['to']}({x.get('condition') or '常规'})" for x in rec["evolves_to"]))
            if rec.get("final_evolution"):
                lines.append("  * 最终进化: " + "；".join(
                    f"{x['final']}({x.get('condition') or '常规'})" for x in rec["final_evolution"]))
            types = rec.get("types") or []
            chart = rec.get("type_chart") or []
            for t in types:
                atk = {}
                dfs = {}
                for row in chart:
                    try:
                        mult = float(row.get("mult"))
                    except (TypeError, ValueError):
                        continue
                    if mult == 1:
                        continue
                    if row.get("from") == t:
                        atk.setdefault(mult, []).append(row.get("to"))
                    if row.get("to") == t:
                        dfs.setdefault(mult, []).append(row.get("from"))
                if atk:
                    lines.append("  * %s进攻: " % t + "、".join(
                        "%sx[%s]" % (m, "、".join(sorted(set(v))))
                        for m, v in sorted(atk.items(), reverse=True)))
                if dfs:
                    lines.append("  * %s防守: " % t + "、".join(
                        "%sx[%s]" % (m, "、".join(sorted(set(v))))
                        for m, v in sorted(dfs.items(), reverse=True)))
            if rec.get("narrative"):
                for n in rec["narrative"]:
                    ev = ("(证据: %s)" % n["evidence"]) if n.get("evidence") else ""
                    lines.append("  * 生态关系: %s【%s】%s" % (
                        NARRATIVE_CN.get(n.get("rel"), n.get("rel")), n.get("other"), ev))
            return "\n".join(lines)
        if label == "Move":
            return f"【招式】{rec.get('name')} 属性:{rec.get('type')} 分类:{rec.get('category')} 威力:{rec.get('power')} 命中:{rec.get('accuracy')} 说明:{rec.get('description')}"
        if label == "Ability":
            return f"【特性】{rec.get('name')} 第{rec.get('generation')}世代 说明:{rec.get('description')}"
        return ""

    def ask(self, question, top_k=5, use_graph=True):
        evidence = self.retrieve(question, top_k=top_k)
        facts = self.get_entity_facts(evidence) if use_graph else []

        context_parts = []
        if facts:
            fact_texts = [self.format_fact(f) for f in facts]
            context_parts.append("【图谱结构化事实】\n" + "\n\n".join(x for x in fact_texts if x))
        if evidence:
            context_parts.append("【检索文本块】\n" + "\n\n".join(e["text"] for e in evidence))

        context = "\n\n".join(context_parts)
        prompt = self.prompt_template.format(
            query_text=question, context=context, examples=""
        )
        resp = self.llm.invoke(prompt)
        return {
            "answer": resp.content,
            "evidence": evidence,
            "facts": facts,
            "mode": "graph_rag" if use_graph else "naive_rag",
        }

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