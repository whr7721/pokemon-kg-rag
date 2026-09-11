# -*- coding: utf-8 -*-
"""数据质量体检：字段完整率 / 关系覆盖率 / 向量覆盖 / 索引与约束清单。

索引或约束缺失时以非零码退出，可作为建库后与交付前的门槛检查。

用法（仓库根）：
    python scripts/check_quality.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from common import neo_http  # noqa: E402

REQUIRED_CONSTRAINTS = [
    "Ability_id", "EggGroup_id", "Form_id", "Move_id", "Pokemon_id",
    "RegionDex_id", "Type_id", "chunk_id", "pokemon_id",
]
REQUIRED_INDEXES = [
    "pokemonFulltext", "abilityFulltext", "moveFulltext", "formFulltext",
    "embedding_Chunk",
]
FIELD_CHECKS = [
    ("Pokemon", "description"), ("Pokemon", "text"),
    ("Form", "text"),
    ("Move", "description"), ("Move", "text"),
    ("Ability", "effect"), ("Ability", "text"),
]
COVERAGE_CHECKS = [
    ("Pokemon 无形态", "MATCH (p:Pokemon) WHERE NOT (p)-[:HAS_FORM]->() RETURN count(p)"),
    ("Form 无属性", "MATCH (f:Form) WHERE NOT (f)-[:HAS_TYPE]->() RETURN count(f)"),
    ("Form 无特性", "MATCH (f:Form) WHERE NOT (f)-[:HAS_ABILITY]->() RETURN count(f)"),
    ("Pokemon 无进化边", "MATCH (p:Pokemon) WHERE NOT (p)-[:EVOLVES_TO]->() "
                        "AND NOT ()-[:EVOLVES_TO]->(p) RETURN count(p)"),
    ("Pokemon 无受击块", "MATCH (p:Pokemon) WHERE NOT EXISTS { "
                        "MATCH (:Chunk {chunk_id: 'hitprofile|' + p.pokedex_id}) } RETURN count(p)"),
]


def main():
    missing = []

    print("== 字段完整率 ==")
    for label, prop in FIELD_CHECKS:
        total, blank = neo_http.query(
            f"MATCH (n:`{label}`) RETURN count(n), "
            f"sum(CASE WHEN n.`{prop}` IS NULL OR n.`{prop}` = '' THEN 1 ELSE 0 END)"
        )[0]
        pct = (total - blank) / total * 100 if total else 0.0
        print(f"  {label}.{prop:12} {total - blank}/{total} ({pct:.1f}%)")

    print("\n== 关系覆盖率 ==")
    for title, stmt in COVERAGE_CHECKS:
        print(f"  {title:16} {neo_http.scalar(stmt)}")

    print("\n== 向量覆盖 ==")
    total = neo_http.scalar("MATCH (c:Chunk) RETURN count(c)")
    embedded = neo_http.scalar("MATCH (c:Chunk) WHERE c.embedding IS NOT NULL RETURN count(c)")
    print(f"  Chunk 合计 {embedded}/{total}")
    for kind, cnt, emb in neo_http.query(
            "MATCH (c:Chunk) RETURN coalesce(c.kind, '?') AS k, count(c), count(c.embedding) "
            "ORDER BY count(c) DESC"):
        print(f"    {kind:14} {emb}/{cnt}")

    print("\n== 索引与约束 ==")
    have_constraints = {r[0] for r in neo_http.query("SHOW CONSTRAINTS YIELD name RETURN name")}
    have_indexes = {r[0] for r in neo_http.query("SHOW INDEXES YIELD name RETURN name")}
    for name in REQUIRED_CONSTRAINTS:
        ok = name in have_constraints
        print(f"  [{'OK' if ok else 'MISSING'}] constraint {name}")
        if not ok:
            missing.append(name)
    for name in REQUIRED_INDEXES:
        ok = name in have_indexes
        print(f"  [{'OK' if ok else 'MISSING'}] index      {name}")
        if not ok:
            missing.append(name)

    if missing:
        print(f"\n缺失 {len(missing)} 项：{', '.join(missing)}")
        return 1
    print("\n数据质量检查通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
