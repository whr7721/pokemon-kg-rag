# -*- coding: utf-8 -*-
"""Chunk 装载：从产物 out/chunks 装载 Chunk+DESCRIBES；--db 经 Query API 直写。"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "build_out"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

import chunker          # 仓库内 src/chunker.py
from common import neo_http  # 仓库内 common/neo_http.py

def load_rows(out):
    return [{"chunk_id": o["id"], "entity_type": o.get("entity_label"),
             "entity_id": o.get("entity_id"), "name_zh": o.get("name_zh") or "",
             "text": o.get("text") or ""} for o in chunker.iter_entity_chunks(out)]

def push_db(rows, batch=200):
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        neo_http.query("UNWIND $rows AS row MERGE (c:Chunk {chunk_id: row.chunk_id}) "
                       "SET c.entity_type=row.entity_type, c.entity_id=row.entity_id, "
                       "c.name_zh=row.name_zh, c.text=row.text", {"rows": part})
        if part:
            lbl = part[0]["entity_type"]
            neo_http.query(f"UNWIND $rows AS row MATCH (c:Chunk {{chunk_id: row.chunk_id}}) "
                           f"MATCH (t:`{lbl}` {{id: row.entity_id}}) MERGE (c)-[:DESCRIBES]->(t)",
                           {"rows": part})
        if (i // batch) % 20 == 0:
            print("已装载", min(i + batch, len(rows)), "/", len(rows))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--db", action="store_true")
    args = ap.parse_args()
    rows = load_rows(args.out)
    print("待装载 entity 块:", len(rows))
    if args.db:
        push_db(rows)

if __name__ == "__main__":
    main()
