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
from neo4j_graphrag.generation import RagTemplate
from neo4j_graphrag.llm import OpenAILLM
from neo4j_graphrag.retrievers import VectorCypherRetriever
from neo4j_graphrag.types import RetrieverResultItem

from embedder import ApiEmbedder
from graph_access import ALIASES, METHOD_CN, GraphAccess, alias_normalize

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
               [x IN collect(DISTINCT p.name_zh) WHERE x IS NOT NULL][..30] AS pokemon_list
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


RRF_K = 60   # Reciprocal Rank Fusion 平滑常数，越大越平缓

# 查询感知的检索计划（PolyG）：labels 过滤实体类型，need 裁剪事实段落，
# paths 控制证据路径跳数，expand 决定图引导召回的扩展规则。
PLANS = {
    "evolution": {"labels": {"Pokemon"}, "need": {"types", "abilities", "evolution"},
                  "paths": 2, "expand": "evolution"},
    "counter": {"labels": {"Type", "Pokemon"}, "need": {"types"},
                "paths": 0, "expand": "counter"},
    "ability": {"labels": {"Ability", "Pokemon"}, "need": {"abilities"},
                "paths": 1, "expand": None},
    "move": {"labels": {"Move"}, "need": {"move"}, "paths": 0, "expand": None},
    "open": {"labels": None, "need": None, "paths": 1, "expand": None},
}


def rrf_fuse(rank_lists, k=RRF_K):
    """Reciprocal Rank Fusion：按各路名次倒数求和后重排。

    向量余弦与全文 BM25 的分数尺度不可比，直接加权求和需要额外归一化和调参；
    RRF 只看名次，天然规避这个问题。缺实体键的条目直接丢弃（无法去重对齐）。
    单路内同一实体只计最高名次一次，避免单个实体的多个低位分块因重复累加而挤掉真正相关的实体。
    """
    scores, items = {}, {}
    for rank_list in rank_lists:
        seen_in_list = set()
        for rank, item in enumerate(rank_list):
            key = (item.metadata.get("entity_label"), item.metadata.get("entity_id"))
            if not all(key):
                continue
            if key in seen_in_list:
                continue
            seen_in_list.add(key)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank + 1)
            items.setdefault(key, item)
    return [items[key] for key in sorted(scores, key=lambda x: -scores[x])]


class PokemonGraphRAG:
    def __init__(self, graph=None):
        self.graph = graph or GraphAccess()
        self.fulltext_indexes = FULLTEXT_INDEXES
        self._fulltext_warned = set()
        self.rrf_k = int(os.getenv("RRF_K") or RRF_K)
        self.embedder = ApiEmbedder()
        self.retriever = VectorCypherRetriever(
            self.graph.driver,
            VECTOR_INDEX,
            RETRIEVAL_QUERY,
            self.embedder,
            result_formatter=record_formatter,
            neo4j_database=self.graph.db,
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
    def _plan(question):
        """按问题意图返回检索计划（PolyG）。"""
        if any(k in question for k in ("招式", "技能")) and "威力" in question:
            return PLANS["move"]
        if "特性" in question:
            return PLANS["ability"]
        if any(k in question for k in ("进化", "最终进化")):
            return PLANS["evolution"]
        if any(k in question for k in ("克制", "弱点", "弱于", "怕", "天敌")) or (
            any(t in question for t in TYPES18) and any(k in question for k in ("攻击", "免疫"))
        ):
            return PLANS["counter"]
        return PLANS["open"]

    def _name_hits(self, question, labels=None, limit=10):
        """全文索引按实体名召回，并把只与问题完全匹配的实体留下。

        labels 来自检索计划；索引缺失或查询报错时不静默失败，打印警告并降级。
        """
        question = alias_normalize(question)
        out = []
        for index in self.fulltext_indexes:
            try:
                rows = self.graph.run(
                    NAME_HIT_QUERY.format(index=index),
                    query_text=question,
                    limit=max(limit, 20),
                )
            except Exception as exc:
                if index not in self._fulltext_warned:
                    self._fulltext_warned.add(index)
                    logger.warning(
                        "全文索引 '%s' 不可用，本次降级为仅向量召回：%s", index, exc
                    )
                continue
            for data in rows:
                if labels and data.get("entity_label") not in labels:
                    continue
                name = str(data.get("entity_name") or "")
                if not name or name not in question:
                    continue
                out.append(record_formatter(data))
        # 错字/近似特性兜底：若计划要求 Ability 但未命中任何特性，按编辑距离找相近特性补入
        if labels and "Ability" in labels and not any(it.metadata.get("entity_label") == "Ability" for it in out):
            for gname in self.graph.guess_ability(question)[:3]:
                out.append(record_formatter({
                    "text": f"特性：{gname}（相近匹配）",
                    "kind": "Ability", "entity_id": f"ability:{gname}",
                    "entity_label": "Ability", "entity_name": gname, "score": 2.0,
                }))
        out.sort(key=lambda it: it.metadata.get("score") or 0, reverse=True)
        return out

    def retrieve(self, question, top_k=8):
        """三路召回 + RRF 融合：向量 / 实体名全文 / 图扩展（KG²RAG）。"""
        question = alias_normalize(question)
        plan = self._plan(question)
        vector = list(self.retriever.search(query_text=question, top_k=top_k * 4).items)
        by_name = self._name_hits(question, labels=plan["labels"], limit=top_k * 2)
        expanded = self._graph_expand(vector + by_name, plan, limit=top_k * 2)
        fused = rrf_fuse([vector, by_name, expanded], k=self.rrf_k)
        return [{"text": it.content, "metadata": it.metadata} for it in fused[:top_k]]

    def _graph_expand(self, items, plan, limit=8):
        """KG²RAG：按计划的扩展规则找相关实体，再取它们的文本块。"""
        rule = plan.get("expand")
        if not rule:
            return []
        seeds = []
        for item in items:
            md = item.metadata
            eid = md.get("entity_id")
            if md.get("entity_label") == "Pokemon" and eid and eid not in seeds:
                seeds.append(eid)
            if len(seeds) >= 2:
                break
        if not seeds:
            return []
        ids = list(seeds)
        for eid in seeds:
            for rid in self.graph.related_ids(eid, rule, limit=limit):
                if rid not in ids:
                    ids.append(rid)
        return [record_formatter(row) for row in self.graph.chunks_of(ids[:limit], limit=limit)]

    def format_paths(self, rows):
        """PathRAG：把进化路径渲染为带条件的箭头串。"""
        lines = []
        for row in rows or []:
            names, rels = row.get("names") or [], row.get("rels") or []
            if len(names) < 2:
                continue
            text = names[0]
            for i, rel in enumerate(rels):
                step = "、".join(x for x in (
                    METHOD_CN.get(rel.get("method"), rel.get("method") or ""),
                    rel.get("condition") or "") if x)
                text += f"--[{step or '特殊'}]--> {names[i + 1]}"
            lines.append("  * " + text)
        return "【图谱证据路径】\n" + "\n".join(lines) if lines else ""

    def _move_power_facts(self, question):
        """招式+威力条件问题：直接从图里取候选招式作为结构化事实。"""
        m = re.search(r"(?:大于|超过|不低于|>=|>)\s*(\d+)", question)
        if not m or not any(k in question for k in ("招式", "技能")):
            return []
        wanted_types = [t for t in TYPES18 if t in question]
        moves = self.graph.run(
            MOVE_POWER_QUERY,
            threshold=int(m.group(1)),
            move_types=wanted_types or None,
        )
        return [{"label": "MoveFilter", "data": {"moves": moves}}] if moves else []

    def get_entity_facts(self, items, limit=3, labels=None):
        seen = set()
        entities = []
        # 排序优先级：
        # 0: 实体名在问题中直接出现的（提问主体，如“电击魔兽”“皮卡丘”，必不可漏）
        # 1: 符合核心标签的实体（如问特性时的 Ability）
        # 2: 其他辅助实体
        candidate_items = items
        def _item_priority(it):
            md = it.get("metadata") or {}
            name = str(md.get("entity_name") or "")
            elab = md.get("entity_label")
            # 提问主体排第一
            is_subject = 1 if name and len(name) >= 2 and (name in getattr(self, "_cur_question", "") or name in str(it.get("text", ""))) else 2
            # 主标签排第二
            is_label = 0 if labels and elab in labels else 1
            return (is_subject, is_label)
        candidate_items = sorted(items, key=_item_priority)

        for it in candidate_items:
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
            rows = self.graph.run(query, eid=eid)
            if rows:
                facts.append({"label": label, "data": rows[0]})
        return facts

    def format_fact(self, fact, need=None):
        """渲染单条图谱事实。need 为 None 时输出全部段落，否则只输出命中的部分。"""
        rec, label = fact.get("data") or {}, fact.get("label")
        if not rec:
            return ""
        if label == "Pokemon":
            types_str = " / ".join(rec.get("types") or [])
            lines = [f"【宝可梦】{rec.get('name')}(编号:{rec.get('id')}) 属性:{types_str} 分类:{rec.get('category','')}"]
            if (need is None or "abilities" in need) and rec.get("abilities"):
                lines.append("  * 特性: " + "、".join(
                    f"{a['name']}(隐藏)" if as_bool(a.get("hidden")) else a['name']
                    for a in rec["abilities"]))
            if need is None and rec.get("egg_groups"):
                lines.append("  * 蛋群: " + "、".join(rec["egg_groups"]))
            if (need is None or "evolution" in need) and rec.get("evolves_from"):
                lines.append("  * 前置进化: " + "；".join(
                    f"{x['from']}({x.get('condition') or '常规'})" for x in rec["evolves_from"]))
            if (need is None or "evolution" in need) and rec.get("evolves_to"):
                lines.append("  * 后续进化: " + "；".join(
                    f"{x['to']}({x.get('condition') or '常规'})" for x in rec["evolves_to"]))
            if (need is None or "evolution" in need) and rec.get("final_evolution"):
                lines.append("  * 最终进化: " + "；".join(
                    f"{x['final']}({x.get('condition') or '常规'})" for x in rec["final_evolution"]))
            types = rec.get("types") if (need is None or "types" in need) else []
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
            if need is None and rec.get("narrative"):
                for n in rec["narrative"]:
                    ev = ("(证据: %s)" % n["evidence"]) if n.get("evidence") else ""
                    lines.append("  * 生态关系: %s【%s】%s" % (
                        NARRATIVE_CN.get(n.get("rel"), n.get("rel")), n.get("other"), ev))
            return "\n".join(lines)
        if label == "Move":
            return f"【招式】{rec.get('name')} 属性:{rec.get('type')} 分类:{rec.get('category')} 威力:{rec.get('power')} 命中:{rec.get('accuracy')} 说明:{rec.get('description')}"
        if label == "Ability":
            eff = rec.get("effect") or rec.get("description") or ""
            lines = [f"【特性】{rec.get('name')} 说明:{eff}"]
            pkm = rec.get("pokemon_list") or []
            if pkm:
                lines.append("  * 拥有该特性的宝可梦: " + "、".join(str(x) for x in pkm if x))
            return "\n".join(lines)
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
        question = alias_normalize(question)
        self._cur_question = question
        plan = self._plan(question)
        evidence = self.retrieve(question, top_k=top_k)
        fact_labels = plan["labels"]
        flimit = 4 if fact_labels and "Ability" in fact_labels else 3
        facts = self.get_entity_facts(evidence, limit=flimit, labels=fact_labels) if use_graph else []
        if use_graph and not facts and fact_labels:
            # 标签过滤过严时（如“某宝可梦有哪些特性”被判为 Ability，而召回到的是 Pokemon）
            # 回退到不限定标签，宁可多带事实也不要因过滤而变“资料不足”。
            facts = self.get_entity_facts(evidence, limit=flimit)
        schema_facts = self._move_power_facts(question) if use_graph else []
        if schema_facts:
            facts = schema_facts + [f for f in facts if f["label"] != "Move"]

        context_parts = []
        if facts:
            fact_texts = [self.format_fact(f, need=plan["need"]) for f in facts]
            context_parts.append("【图谱结构化事实】\n" + "\n\n".join(x for x in fact_texts if x))
        # 策略第二跳：若问及携带/适合/对策或特性，调图谱把命中宝可梦的全部特性机制说明查出注入
        if use_graph and (any(w in question for w in ("应对", "携带", "适合", "对策", "面对")) or "特性" in question):
            pkm_names = [f["data"].get("name") for f in facts if f["label"] == "Pokemon" and f["data"].get("name")]
            mech = []
            for pname in pkm_names[:2]:
                ab_rows = self.graph.ability_rows(pname)
                for ab, info in ab_rows.items():
                    tag = "隐藏" if info.get("hidden") else "普通"
                    eff = str(info.get("eff") or info.get("dsc") or "").replace("\n", " ").strip()
                    if eff:
                        mech.append(f"  * 「{pname}」特性【{ab}】（{tag}）：{eff[:120]}")
            if mech:
                context_parts.append("【特性机制详情（供策略自判）】\n" + "\n".join(mech))
        if use_graph and plan["paths"]:
            for f in facts:
                if f["label"] == "Pokemon":
                    path_text = self.format_paths(
                        self.graph.paths(f["data"].get("id"), depth=plan["paths"]))
                    if path_text:
                        context_parts.append(path_text)
                    break
        if evidence:
            context_parts.append("【检索文本块】\n" + "\n\n".join(e["text"] for e in evidence))
        # 相近特性提示：当输入特性名不精确时，显式把候选相近特性给到模型
        # 相近特性提示：仅当问题询问特性持有者但特性名非官方名称时才给提示；策略题不提示，避免误导模型
        if use_graph and "特性" in question and not any(w in question for w in ("携带", "适合", "应对", "对策", "面对")):
            if not any(f["label"] == "Ability" and f["data"].get("name") in question for f in facts):
                guessed = self.graph.guess_ability(question)
                if len(guessed) >= 2:
                    context_parts.append(
                        f"【相近特性提示】若输入特性名非官方规范名称（如“蓄水”），请结合上下文列出的相近特性（{'、'.join(guessed[:2])}）一并说明拥有它们的宝可梦。"
                    )

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
            rows = self.graph.run(
                "SHOW INDEXES YIELD name, state WHERE name IN $names RETURN name, state",
                names=names,
            )
            states = {r["name"]: r["state"] for r in rows}
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
        return self.graph.subgraph(items, limit)

    def close(self):
        self.graph.close()
