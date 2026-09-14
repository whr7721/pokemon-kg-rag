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
