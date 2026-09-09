# -*- coding: utf-8 -*-
"""文本块（读取 build_graph 产物 out/chunks/*.jsonl）。"""
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "build_out"

def count_chunks(out):
    stat = {}
    for name in ("entity_chunks.jsonl", "relation_chunks.jsonl"):
        p = Path(out) / "chunks" / name
        stat[name] = sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else None
    return stat

def iter_entity_chunks(out):
    p = Path(out) / "chunks" / "entity_chunks.jsonl"
    for line in p.open(encoding="utf-8"):
        if line.strip():
            yield json.loads(line)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()
    print(count_chunks(args.out))

if __name__ == "__main__":
    main()
