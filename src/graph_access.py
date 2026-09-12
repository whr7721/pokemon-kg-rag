# -*- coding: utf-8 -*-
"""统一图访问层：所有 Neo4j 连接、共享 Cypher 查询和图数据缓存集中在这里。

MultiQA 与 GraphRAG 都只调用本模块，不再各自创建 driver 或复制相同的图查询。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

ALIASES = {
    "板匙蛇": "饭匙蛇",   # Seviper 旧称 -> 官方译名
}


def alias_normalize(question: str) -> str:
    q = question
    for k, v in ALIASES.items():
        q = q.replace(k, v)
    return q

VECTOR_INDEX = os.getenv("VECTOR_INDEX") or "embedding_Chunk"
FULLTEXT_INDEXES = [i.strip() for i in (
    os.getenv("FULLTEXT_INDEXES")
    or "pokemonFulltext,abilityFulltext,moveFulltext,formFulltext"
).split(",") if i.strip()]

TYPES18 = ("一般", "格斗", "飞行", "毒", "地面", "岩石", "虫", "幽灵", "钢",
           "火", "水", "草", "电", "超能力", "冰", "龙", "恶", "妖精")

NARRATIVE_RELS = ("RIVAL_OF", "PREDATES_ON", "COMPETES_WITH", "ALLIED_WITH",
                  "COMMENSAL_OF", "MENTOR_OF", "SYMBIOTIC_WITH")
NARRATIVE_CN = {
    "RIVAL_OF": "宿敌", "PREDATES_ON": "捕食", "COMPETES_WITH": "竞争",
    "ALLIED_WITH": "结盟", "COMMENSAL_OF": "共生", "MENTOR_OF": "师徒",
    "SYMBIOTIC_WITH": "互利共生",
}

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

METHOD_CN = {
    "level": "升级", "machine": "招式学习器", "egg": "蛋招式遗传",
    "trade": "连接交换", "item": "使用道具", "friendship": "亲密度",
    "beauty": "美丽度", "time": "时段条件", "weather": "天气条件",
    "location": "特定地点", "gender": "性别条件", "other": "特殊条件",
}

# 进化链证据路径（PathRAG）；跳数不能参数化，只能字符串插值，深度白名单控制。
PATH_QUERY_TMPL = """
MATCH p = (a:Pokemon {pokedex_id: $eid})-[:EVOLVES_TO*1..%d]->(b)
RETURN [n IN nodes(p) | coalesce(n.name_zh, n.id, '')] AS names,
       [r IN relationships(p) | {method: r.method, condition: r.condition}] AS rels
LIMIT $limit
"""

# 图引导召回扩展（KG²RAG）：进化家族 / 克制候选。
RELATED_EVOLUTION_QUERY = """
MATCH (a:Pokemon {pokedex_id: $eid})-[:EVOLVES_TO*1..2]-(b:Pokemon)
RETURN DISTINCT b.pokedex_id AS eid
LIMIT $limit
"""

DEFAULT_TYPES_QUERY = """
MATCH (p:Pokemon {pokedex_id: $eid})-[hf:HAS_FORM]->(:Form)-[:HAS_TYPE]->(t:Type)
WHERE toString(hf.default) IN ['True', 'true']
RETURN DISTINCT t.name_zh AS t
"""

# 拥有指定属性（默认形态）的宝可梦。克制关系必须由完整受击倍率判定决定，
# 不能靠单属性：地面克制火，但飞行免疫地面，喷火龙并不怕地面。
TYPE_CANDIDATES_QUERY = """
MATCH (p:Pokemon)-[hf:HAS_FORM]->(:Form)-[:HAS_TYPE]->(t:Type)
WHERE t.name_zh IN $types AND p.pokedex_id <> $eid
  AND toString(hf.default) IN ['True', 'true']
RETURN DISTINCT p.pokedex_id AS eid
LIMIT $limit
"""

CHUNKS_BY_ENTITY_QUERY = """
MATCH (c:Chunk)-[:DESCRIBES]->(e:Pokemon)
WHERE e.pokedex_id IN $ids
RETURN c.text AS text,
       coalesce(c.entity_type, c.kind, '') AS kind,
       e.pokedex_id AS entity_id,
       'Pokemon' AS entity_label,
       coalesce(e.name_zh, '') AS entity_name,
       0.0 AS score
ORDER BY CASE WHEN c.kind = 'hit-profile' THEN 0 WHEN c.kind = 'relation' THEN 1 ELSE 2 END
LIMIT $limit
"""


def get_driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
        notifications_disabled_classifications=["DEPRECATION"],
    )


class GraphAccess:
    """Neo4j 的唯一访问入口，并缓存图级共享数据。"""

    def __init__(self, driver=None):
        self.driver = driver or get_driver()
        self.db = os.getenv("NEO4J_DB") or "neo4j"
        self._names = None
        self._abilities = None
        self._chart = None
        self._pkm_types = None
        self._evo = None
        self._narr = None

    def run(self, query, **params):
        with self.driver.session(database=self.db) as s:
            return s.run(query, **params).data()

    def names(self):
        if self._names is None:
            self._names = sorted(r["n"] for r in self.run("MATCH (p:Pokemon) RETURN p.name_zh AS n"))
        return self._names

    def abilities(self):
        if self._abilities is None:
            self._abilities = [r["n"] for r in self.run("MATCH (a:Ability) RETURN a.name_zh AS n")]
        return self._abilities

    def ability_meta(self, names):
        rows = self.run("""
            UNWIND $names AS n
            MATCH (a:Ability {name_zh: n})
            OPTIONAL MATCH (f:Form)-[:HAS_ABILITY]->(a)
            RETURN n AS name, a.description AS dsc,
                   count(DISTINCT f.pokedex_id) AS holders
        """, names=names)
        return {r["name"]: r for r in rows}

    def type_chart(self):
        """攻击属性名 -> {防御属性名: 倍率}（HITS_TYPE 攻击->防御）。"""
        if self._chart is None:
            nm = {r["id"]: r["nm"] for r in self.run("MATCH (t:Type) RETURN t.id AS id, t.name_zh AS nm")}
            self._chart = {}
            for r in self.run("MATCH (a)-[h:HITS_TYPE]->(b) RETURN a.id AS a, b.id AS b, h.multiplier AS m"):
                atk, dfn = nm.get(r["a"]), nm.get(r["b"])
                if atk and dfn:
                    self._chart.setdefault(atk, {})[dfn] = float(r["m"])
        return self._chart

    def pokemon_default_types(self):
        if self._pkm_types is None:
            rows = self.run("""
                MATCH (p:Pokemon)-[:HAS_FORM]->(f:Form)
                OPTIONAL MATCH (f)-[:HAS_TYPE]->(t:Type)
                WITH p, f, collect(DISTINCT t.name_zh) AS types
                WHERE f.is_default IS NULL OR f.is_default = true OR toString(f.is_default) IN ['True', 'true', '1']
                RETURN p.name_zh AS name, types
            """)
            merged = {}
            for r in rows:
                merged.setdefault(r["name"], r["types"])
            self._pkm_types = merged
        return self._pkm_types

    def evo_graph(self):
        if self._evo is None:
            g = {"out": {}, "in": {}, "cond": {}}
            for r in self.run("MATCH (a)-[r:EVOLVES_TO]->(b) RETURN a.name_zh AS a, b.name_zh AS b, r.condition AS c"):
                g["out"].setdefault(r["a"], []).append(r["b"])
                g["in"].setdefault(r["b"], []).append(r["a"])
                g["cond"][(r["a"], r["b"])] = r["c"]
            self._evo = g
        return self._evo

    def narrative_edges(self):
        if self._narr is None:
            self._narr = self.run(
                "MATCH (a:Pokemon)-[r]->(b:Pokemon) "
                "WHERE type(r) IN $rels "
                "RETURN a.name_zh AS a, b.name_zh AS b, type(r) AS t, "
                "properties(r).evidence AS ev, properties(r).confidence AS cf",
                rels=list(NARRATIVE_RELS))
        return self._narr

    def ability_rows(self, name):
        rows = self.run("""
            MATCH (p:Pokemon {name_zh: $n})-[:HAS_FORM]->(f:Form)-[r:HAS_ABILITY]->(a:Ability)
            RETURN a.name_zh AS ab, a.description AS dsc, a.effect AS eff,
                   coalesce(f.is_default,'') AS def, coalesce(r.hidden,'False') AS hd
        """, n=name)
        best = {}
        for r in rows:
            is_def = r["def"] in ("True", "") or str(r["def"]).lower() == "true"
            hidden = str(r["hd"]).lower() in ("true", "1", "yes")
            cur = best.get(r["ab"])
            if cur is None or (is_def and not cur.get("def")):
                best[r["ab"]] = {"hidden": hidden, "def": is_def,
                                 "dsc": r["dsc"] or "", "eff": r["eff"] or ""}
            elif is_def:
                best[r["ab"]]["hidden"] = best[r["ab"]]["hidden"] or hidden
        return best

    def guess_ability(self, question: str):
        """针对错字或俗称特性（悬浮->飘浮、蓄水->储水），按编辑距离找最接近的特性名。"""
        abilities = [a for a in self.abilities() if 2 <= len(a) <= 4]
        cands = []
        for ab in abilities:
            L = len(ab)
            for width in (max(1, L - 1), L, L + 1):
                for i in range(0, len(question) - width + 1):
                    sub = question[i:i + width]
                    d = sum(1 for c1, c2 in zip(ab, sub) if c1 != c2) + abs(len(ab) - len(sub))
                    if d <= 1 and any(c in ab for c in sub):
                        overlap = len(set(ab) & set(sub))
                        cands.append((overlap, -d, ab))
                        break
        cands.sort(key=lambda x: (-x[0], -x[1], x[2]))
        seen, res = set(), []
        for o, d, a in cands:
            if a not in seen:
                seen.add(a)
                res.append(a)
        return res[:4]

    def paths(self, eid, depth=2, limit=10):
        """进化链证据路径。depth 只接受 1/2/3，其余按 2 处理。"""
        depth = depth if depth in (1, 2, 3) else 2
        return self.run(PATH_QUERY_TMPL % depth, eid=eid, limit=limit)

    def related_ids(self, eid, rule, limit=8):
        """图引导召回的相关实体（rule ∈ {'evolution', 'counter'}）。"""
        if rule == "evolution":
            return [r["eid"] for r in self.run(RELATED_EVOLUTION_QUERY, eid=eid, limit=limit)]
        if rule != "counter":
            return []
        types = [r["t"] for r in self.run(DEFAULT_TYPES_QUERY, eid=eid)]
        if not types:
            return []
        chart = self.type_chart()
        strong = []
        for att, defenses in chart.items():
            mult = 1.0
            for t in types:
                mult *= float(defenses.get(t, 1.0))
            if mult >= 2:
                strong.append(att)
        if not strong:
            return []
        return [r["eid"] for r in self.run(
            TYPE_CANDIDATES_QUERY, types=strong, eid=eid, limit=limit)]

    def chunks_of(self, ids, limit=12):
        """取指定宝可梦的文本块，形状与 record_formatter 的输入一致。"""
        if not ids:
            return []
        return self.run(CHUNKS_BY_ENTITY_QUERY, ids=list(ids), limit=limit)

    def subgraph(self, items, limit=3):
        nodes = {}
        edges = []
        for item in items[:limit]:
            meta = item.get("metadata") or {}
            label, eid = meta.get("entity_label"), meta.get("entity_id")
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
                fid, tid = f"{fl}:{fn}", f"{tl}:{tn}"
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
