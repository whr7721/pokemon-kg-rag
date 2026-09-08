# -*- coding: utf-8 -*-
"""将 src/build_engine.py 的 build_out 产物导入 Neo4j（阶段一增强 Schema）。

用法（仓库根，先建产物再入库）：
    python src/build_graph.py                  # 生成 build_out
    python scripts/load_engine_out.py --clean  # 全量清库后导入
    python scripts/embed_vectors.py            # 本地 BGE-M3 向量化
    python scripts/setup_indexes.py            # 向量/约束索引

说明：
    - 默认拒绝导入非空库；只有显式加 --clean 才会清空当前 Neo4j 数据库。
    - 只导入 entity_chunks（约 3 万实体块）；relation_chunks 由在线 GraphRAG
      邻域查询负责，不再重复向量化，避免语料膨胀。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

load_dotenv(ROOT / ".env")

BATCH_NODES = 500
BATCH_RELS = 500
BATCH_CHUNKS = 500


def iter_jsonl(path):
    if not path.exists():
        return
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def prop_value(v):
    if v is None or isinstance(v, str) or isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    return json.dumps(v, ensure_ascii=False)


def flatten_props(row, exclude=()):
    return {k: prop_value(v) for k, v in row.items() if k not in exclude}


def run_in_batches(session, statement, rows, batch):
    for i in range(0, len(rows), batch):
        session.run(statement, {"rows": rows[i:i + batch]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "build_out"))
    ap.add_argument("--clean", action="store_true", help="清空整库后重建（共享库请约定单独执行）")
    args = ap.parse_args()
    out = Path(args.out)

    entity_dir = out / "entities"
    relation_dir = out / "relations"
    chunk_path = out / "chunks" / "entity_chunks.jsonl"

    node_files = sorted(entity_dir.glob("entity_*.jsonl"))
    rel_files = sorted(relation_dir.glob("rel_*.jsonl"))
    if not node_files or not rel_files:
        raise SystemExit(f"build_out 不完整：{out}")

    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
        notifications_disabled_classifications=["DEPRECATION"],
    )
    db = os.getenv("NEO4J_DB", "neo4j")

    id_label = {}
    try:
        with driver.session(database=db) as session:
            if args.clean:
                n = session.run("MATCH (n) DETACH DELETE n RETURN count(*) AS c").single()["c"]
                print("已清空旧图，删除节点数 =", n)
            else:
                n = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
                if n:
                    raise SystemExit("当前库非空；正式重建请显式运行：python scripts/load_engine_out.py --clean")

            for path in node_files:
                label = path.stem.replace("entity_", "")
                rows = list(iter_jsonl(path))
                for row in rows:
                    id_label[row["id"]] = label
                payload = [{"id": r["id"], "props": flatten_props(r, exclude={"id"})} for r in rows]
                run_in_batches(
                    session,
                    f"UNWIND $rows AS row MERGE (n:`{label}` {{id: row.id}}) SET n += row.props",
                    payload,
                    BATCH_NODES,
                )
                print(f"节点 {label}: {len(rows)}")

            for path in rel_files:
                rtype = path.stem.replace("rel_", "")
                rows = list(iter_jsonl(path))
                labels = {(id_label.get(r["from"]), id_label.get(r["to"])) for r in rows}
                if len(labels) != 1:
                    raise RuntimeError(f"{rtype} 端点标签不唯一: {labels}")
                (src_label, dst_label), = labels
                key = ""
                if rtype == "LEARNS":
                    key = " {method: row.method}"
                elif rtype == "TYPE_MOD":
                    key = " {defender_type: row.props.defender_type}"
                payload = [{
                    "from": r["from"], "to": r["to"],
                    "props": flatten_props(r, exclude={"from", "to", "type"}),
                } for r in rows]
                stmt = (
                    f"UNWIND $rows AS row "
                    f"MATCH (a:`{src_label}` {{id: row.from}}), (b:`{dst_label}` {{id: row.to}}) "
                    f"MERGE (a)-[r:{rtype}{key}]->(b) "
                    "SET r += row.props"
                )
                run_in_batches(session, stmt, payload, BATCH_RELS)
                print(f"关系 {rtype}: {len(rows)}")

            chunk_rows = list(iter_jsonl(chunk_path))
            payload = [{
                "chunk_id": c["id"], "kind": c.get("kind"),
                "entity_type": c.get("entity_label"), "entity_id": c.get("entity_id"),
                "name_zh": c.get("name_zh", ""), "text": c.get("text", ""),
            } for c in chunk_rows]
            run_in_batches(
                session,
                "UNWIND $rows AS row MERGE (c:Chunk {chunk_id: row.chunk_id}) "
                "SET c.kind=row.kind, c.entity_type=row.entity_type, c.entity_id=row.entity_id, "
                "c.name_zh=row.name_zh, c.text=row.text",
                payload,
                BATCH_CHUNKS,
            )

            by_label = {}
            for c in chunk_rows:
                lbl = c.get("entity_label")
                if lbl in id_label and c.get("entity_id") in id_label:
                    by_label.setdefault(lbl, []).append(c)
            for lbl, rows in by_label.items():
                sub = [{"chunk_id": c["id"], "entity_id": c["entity_id"]} for c in rows]
                run_in_batches(
                    session,
                    "UNWIND $rows AS row "
                    f"MATCH (c:Chunk {{chunk_id: row.chunk_id}}), (t:`{lbl}` {{id: row.entity_id}}) "
                    "MERGE (c)-[:DESCRIBES]->(t)",
                    sub,
                    BATCH_CHUNKS,
                )
                print(f"Chunk DESCRIBES -> {lbl}: {len(sub)}")
            print("实体块装载:", len(chunk_rows))
    finally:
        driver.close()


if __name__ == "__main__":
    main()