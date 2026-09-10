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
import logging
import re
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl
from neo4j_graphrag.generation import RagTemplate
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.retrievers import VectorCypherRetriever
from neo4j_graphrag.types import RetrieverResultItem

from embedder import ApiEmbedder

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logger = logging.getLogger(__name__)

VECTOR_INDEX = os.getenv("VECTOR_INDEX") or "embedding_Chunk"

# 全文索引：按实体标签命名，直接命中实体节点（增强库口径）。
FULLTEXT_INDEXES = [i.strip() for i in (
    os.getenv("FULLTEXT_INDEXES")
    or "pokemonFulltext,abilityFulltext,moveFulltext,formFulltext"
).split(",") if i.strip()]

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
       coalesce(ent.name_zh, ent.form_name, toString(ent.num), '') AS entity_name,
       score AS score
"""

TYPES18 = ("一般", "格斗", "飞行", "毒", "地面", "岩石", "虫", "幽灵", "钢",
           "火", "水", "草", "电", "超能力", "冰", "龙", "恶", "妖精")

# 全文索引直接命中实体节点，需反查该实体的文本块；
# left(..., 700) 与建图切块上限一致，避免把整篇图鉴塞进 Prompt。
NAME_HIT_QUERY = """
CALL db.index.fulltext.queryNodes('{index}', $query_text) YIELD node, score
WITH node, score
OPTIONAL MATCH (par:Pokemon)-[:HAS_FORM]->(node)
WITH node, score,
     CASE WHEN par IS NOT NULL THEN par ELSE node END AS ent,
     CASE WHEN par IS NOT NULL THEN 'Pokemon' ELSE labels(node)[0] END AS elab
OPTIONAL MATCH (c:Chunk)-[:DESCRIBES]->(ent)
WITH ent, elab, score, collect(c.text)[0] AS ctext
RETURN left(coalesce(ctext, ent.text, ''), 700) AS text,
       elab AS kind,
       CASE WHEN elab = 'Pokemon' THEN ent.pokedex_id
            WHEN elab IN ['Move', 'Ability'] THEN ent.id
            ELSE coalesce(ent.pokedex_id, ent.id, '') END AS entity_id,
       elab AS entity_label,
       coalesce(ent.name_zh, ent.form_name, toString(ent.num), '') AS entity_name,
       score AS score
ORDER BY score DESC
LIMIT $limit
"""

MOVE_POWER_QUERY = """
MATCH (m:Move)
WHERE toInteger(m.power) >= $threshold
  AND ($move_types IS NULL OR m.type IN $move_types)
RETURN m.name_zh AS name, m.type AS type, m.category AS category,
       toInteger(m.power) AS power
ORDER BY power DESC
"""

SUBGRAPH_QUERIES = {
    "Pokemon": """
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[:HAS_FORM]->(:Form)-[r:HAS_TYPE|HAS_ABILITY|IN_EGG_GROUP]->(m)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS fl, coalesce(n.name_zh, n.id, '') AS fn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.id, '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (m)-[r:EVOLVES_TO]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS fl, coalesce(m.name_zh, m.id, '') AS fn,
               type(r) AS rel,
               labels(n)[0] AS tl,
               coalesce(n.name_zh, n.id, '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[r:EVOLVES_TO]->(m)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(n)[0] AS fl, coalesce(n.name_zh, n.id, '') AS fn,
               type(r) AS rel,
               labels(m)[0] AS tl,
               coalesce(m.name_zh, m.id, '') AS tn
        UNION
        MATCH (n:Pokemon {pokedex_id: $eid})
        OPTIONAL MATCH (n)-[r]->(m:Pokemon)
        WITH n, r, m
        WHERE r IS NOT NULL AND type(r) IN ['PREDATES_ON','RIVAL_OF','ALLIED_WITH','COMPETES_WITH','COMMENSAL_OF','MENTOR_OF','SYMBIOTIC_WITH']
        RETURN labels(n)[0] AS fl, coalesce(n.name_zh, n.id, '') AS fn,
               type(r) AS rel,
               labels(m)[0] AS tl, coalesce(m.name_zh, '') AS tn
    """,
    "Move": """
        MATCH (n:Move {id: $eid})
        OPTIONAL MATCH (m:Pokemon)-[:HAS_FORM]->(:Form)-[r:LEARNS]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS fl, coalesce(m.name_zh, m.id, '') AS fn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, '') AS tn
        LIMIT 15
    """,
    "Ability": """
        MATCH (n:Ability {id: $eid})
        OPTIONAL MATCH (m:Pokemon)-[:HAS_FORM]->(:Form)-[r:HAS_ABILITY]->(n)
        WITH n, r, m
        WHERE r IS NOT NULL
        RETURN labels(m)[0] AS fl, coalesce(m.name_zh, m.id, '') AS fn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, '') AS tn
        LIMIT 15
    """,
    "Type": """
        MATCH (n:Type {id: $eid})
        OPTIONAL MATCH (n)-[h:HITS_TYPE]->(m:Type)
        WITH n, h, m
        WHERE h IS NOT NULL
        RETURN labels(n)[0] AS fl, coalesce(n.name_zh, n.id, '') AS fn,
               type(h) AS rel,
               labels(m)[0] AS tl, coalesce(m.name_zh, '') AS tn
        UNION
        MATCH (n:Type {id: $eid})
        OPTIONAL MATCH (p:Type)-[r:HITS_TYPE]->(n)
        WITH n, r, p
        WHERE r IS NOT NULL
        RETURN labels(p)[0] AS fl, coalesce(p.name_zh, p.id, '') AS fn,
               type(r) AS rel,
               labels(n)[0] AS tl, coalesce(n.name_zh, '') AS tn
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
            OPTIONAL MATCH (prev:Pokemon)-[r_prev:EVOLVES_TO]->(p)
            OPTIONAL MATCH (p)-[r_next:EVOLVES_TO]->(nxt:Pokemon)
            OPTIONAL MATCH (p)-[:EVOLVES_TO]->(:Pokemon)-[r_final:EVOLVES_TO]->(final:Pokemon)
            RETURN [x IN collect(DISTINCT {from: prev.name_zh, condition: r_prev.condition}) WHERE x.from IS NOT NULL] AS evolves_from,
                   [x IN collect(DISTINCT {to: nxt.name_zh, condition: r_next.condition}) WHERE x.to IS NOT NULL] AS evolves_to,
                   [x IN collect(DISTINCT {final: final.name_zh, condition: r_final.condition}) WHERE x.final IS NOT NULL] AS final_evolution
        }
        CALL (p) {
            OPTIONAL MATCH (p)-[r_narr]->(other:Pokemon)
            WHERE type(r_narr) IN ['PREDATES_ON','RIVAL_OF','ALLIED_WITH','COMPETES_WITH','COMMENSAL_OF','MENTOR_OF','SYMBIOTIC_WITH']
            RETURN [x IN collect(DISTINCT {rel: type(r_narr), other: other.name_zh, evidence: properties(r_narr).evidence})
                    WHERE x.other IS NOT NULL] AS narrative
        }
        RETURN p.name_zh AS name, p.pokedex_id AS id, p.category AS category,
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
        RETURN a.name_zh AS name, a.description AS description,
               coalesce(a.effect, a.description, a.text, '') AS effect,
               a.generation AS generation,
               [x IN collect(DISTINCT p.name_zh) WHERE x IS NOT NULL][..8] AS pokemon_list
    """,
    "Type": """
        MATCH (t:Type {id: $eid})
        OPTIONAL MATCH (t)-[h:HITS_TYPE]->(def:Type)
        OPTIONAL MATCH (att:Type)-[d:HITS_TYPE]->(t)
        RETURN t.name_zh AS name,
               [x IN collect(DISTINCT {to: def.name_zh, mult: h.multiplier}) WHERE x.to IS NOT NULL] AS attacks,
               [x IN collect(DISTINCT {from: att.name_zh, mult: d.multiplier}) WHERE x.from IS NOT NULL] AS defenses
    """,
}

NARRATIVE_CN = {
    "RIVAL_OF": "宿敌", "PREDATES_ON": "捕食", "COMPETES_WITH": "竞争",
    "ALLIED_WITH": "结盟", "COMMENSAL_OF": "共生", "MENTOR_OF": "师徒",
    "SYMBIOTIC_WITH": "互利共生",
}


def as_bool(value):
    """库内布尔字段以字符串形式存储（'True'/'False'），统一解析。"""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "1", "yes")


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
        self.fulltext_indexes = FULLTEXT_INDEXES
        self._fulltext_warned = set()
        self.embedder = ApiEmbedder()
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
        for warn in self.health()["warnings"]:
            logger.warning(warn)

    @staticmethod
    def _candidate_labels(question):
        labels = set()
        if any(k in question for k in ("招式", "技能")) and "威力" in question:
            labels.add("Move")
        elif "特性" in question and not any(t in question for t in TYPES18):
            labels.add("Ability")
        elif any(k in question for k in ("进化", "最终进化")):
            labels.add("Pokemon")
        elif any(t in question for t in TYPES18) and any(
            k in question for k in ("克制", "攻击", "弱点", "弱于", "怕", "免疫")
        ):
            labels.add("Type")
        return labels or None

    def _name_hits(self, question, limit=10):
        """全文索引按实体名召回，并把只与问题完全匹配的实体留下。

        索引缺失或查询报错时不静默失败：打印警告并降级为仅向量召回。
        """
        labels = self._candidate_labels(question)
        out = []
        for index in self.fulltext_indexes:
            try:
                records, _, _ = self.driver.execute_query(
                    NAME_HIT_QUERY.format(index=index),
                    {"query_text": question, "limit": max(limit, 20)},
                    database_=self.db,
                    routing_=RoutingControl.READ,
                )
            except Exception as exc:
                if index not in self._fulltext_warned:
                    self._fulltext_warned.add(index)
                    logger.warning(
                        "全文索引 '%s' 不可用，本次降级为仅向量召回：%s", index, exc
                    )
                continue
            for rec in records:
                data = rec.data()
                if labels and data.get("entity_label") not in labels:
                    continue
                name = str(data.get("entity_name") or "")
                if not name or name not in question:
                    continue
                out.append(record_formatter(data))
        out.sort(key=lambda it: it.metadata.get("score") or 0, reverse=True)
        return out

    def retrieve(self, question, top_k=8):
        # 两路各自多取候选（向量 ×4、实体名 ×2），合并去重后再截断到 top_k，
        # 避免去重后不同实体不足。
        result = self.retriever.search(query_text=question, top_k=top_k * 4)
        items = dedupe_by_entity(
            self._name_hits(question, limit=top_k * 2) + list(result.items),
            limit=top_k,
        )
        return [
            {"text": item.content, "metadata": item.metadata}
            for item in items
        ]

    def _move_power_facts(self, question):
        """招式+威力条件问题：直接从图里取候选招式作为结构化事实。"""
        m = re.search(r"(?:大于|超过|不低于|>=|>)\s*(\d+)", question)
        if not m or not any(k in question for k in ("招式", "技能")):
            return []
        wanted_types = [t for t in TYPES18 if t in question]
        records, _, _ = self.driver.execute_query(
            MOVE_POWER_QUERY,
            {"threshold": int(m.group(1)), "move_types": wanted_types or None},
            database_=self.db,
            routing_=RoutingControl.READ,
        )
        moves = [rec.data() for rec in records]
        return [{"label": "MoveFilter", "data": {"moves": moves}}] if moves else []

    def get_entity_facts(self, items, limit=3, labels=None):
        seen = set()
        entities = []
        for it in items:
            meta = it.get("metadata") or {}
            pair = (meta.get("entity_label"), meta.get("entity_id"))
            if pair[0] and pair[1] and pair not in seen:
                if labels and pair[0] not in labels:
                    continue
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
            lines = [f"【宝可梦】{rec.get('name')}(编号:{rec.get('id')}) 属性:{types_str} 分类:{rec.get('category','')}"]
            if rec.get("abilities"):
                lines.append("  * 特性: " + "、".join(
                    f"{a['name']}(隐藏)" if as_bool(a.get("hidden")) else a['name']
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
            eff = rec.get("effect") or rec.get("description") or ""
            return f"【特性】{rec.get('name')} 说明:{eff}"
        if label == "Type":
            lines = [f"【属性类型】{rec.get('name')}"]
            atk, weak, res = {}, {}, {}
            for row in rec.get("attacks") or []:
                try:
                    mult = float(row.get("mult"))
                except (TypeError, ValueError):
                    continue
                if mult > 1:
                    atk.setdefault(mult, []).append(row.get("to"))
            for row in rec.get("defenses") or []:
                try:
                    mult = float(row.get("mult"))
                except (TypeError, ValueError):
                    continue
                if mult > 1:
                    weak.setdefault(mult, []).append(row.get("from"))
                elif 0 <= mult < 1:
                    res.setdefault(mult, []).append(row.get("from"))
            if atk:
                lines.append("  * 进攻克制: " + "；".join(
                    f"{m:g}倍[{', '.join(sorted(set(v)))}]"
                    for m, v in sorted(atk.items(), reverse=True)))
            if weak:
                lines.append("  * 防守弱点: " + "；".join(
                    f"{m:g}倍[{', '.join(sorted(set(v)))}]"
                    for m, v in sorted(weak.items(), reverse=True)))
            if res:
                lines.append("  * 抵抗/免疫: " + "；".join(
                    f"{m:g}倍[{', '.join(sorted(set(v)))}]"
                    for m, v in sorted(res.items())))
            return "\n".join(lines)
        if label == "MoveFilter":
            moves = rec.get("moves") or []
            return (f"【图谱筛选·招式】共 {len(moves)} 个：" + "；".join(
                f"{x.get('name')}({x.get('type')}/{x.get('category')}/{x.get('power')}威力)"
                for x in moves[:30]))
        return ""

    def ask(self, question, top_k=8, use_graph=True):
        evidence = self.retrieve(question, top_k=top_k)
        fact_labels = self._candidate_labels(question)
        facts = self.get_entity_facts(evidence, labels=fact_labels) if use_graph else []
        if use_graph and not facts and fact_labels:
            # 标签过滤过严时（如“某宝可梦有哪些特性”被判为 Ability，而召回到的是 Pokemon）
            # 回退到不限定标签，宁可多带事实也不要因过滤而变“资料不足”。
            facts = self.get_entity_facts(evidence)
        schema_facts = self._move_power_facts(question) if use_graph else []
        if schema_facts:
            facts = schema_facts + [f for f in facts if f["label"] != "Move"]

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

    def health(self):
        """索引自检快照：启动时打日志，/api/health 对外返回。"""
        names = [VECTOR_INDEX] + self.fulltext_indexes
        try:
            records, _, _ = self.driver.execute_query(
                "SHOW INDEXES YIELD name, state WHERE name IN $names RETURN name, state",
                {"names": names},
                database_=self.db,
                routing_=RoutingControl.READ,
            )
            states = {r["name"]: r["state"] for r in records}
            warns = [f"索引缺失：{n}" for n in names if n not in states]
            warns += [f"索引未就绪：{n}（{s}）" for n, s in states.items() if s != "ONLINE"]
        except Exception as exc:
            warns = [f"索引状态查询失败：{exc}"]
        return {
            "ok": not warns,
            "vector_index": VECTOR_INDEX,
            "fulltext_indexes": self.fulltext_indexes,
            "warnings": warns,
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
                fl, fn, rel, tl, tn = rec["fl"], rec["fn"], rec["rel"], rec["tl"], rec["tn"]
                if not fn or not tn:
                    continue
                fid = f"{fl}:{fn}"
                tid = f"{tl}:{tn}"
                nodes[fid] = {"id": fid, "label": fn, "group": fl}
                nodes[tid] = {"id": tid, "label": tn, "group": tl}
                edges.append({"from": fid, "to": tid, "label": rel})
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
