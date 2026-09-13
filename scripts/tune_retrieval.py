# -*- coding: utf-8 -*-
"""检索参数实验：top_k × RRF_K 网格搜索，指标为 ablation 题集上的召回命中率。

预先对 19 题执行一次向量召回、全文召回与图扩展，网格搜索各参数只在内存中切片和重排，
消除重复网络开销，保证几十毫秒内完成整个网格遍历。

用法（仓库根）：
    python scripts/tune_retrieval.py --out build_out/tune.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from rag import PokemonGraphRAG, rrf_fuse  # noqa: E402
from run_ablation import QUESTIONS  # noqa: E402

TOP_KS = (4, 6, 8, 12)
RRF_KS = (10, 30, 60, 100)
MAX_TOP_K = max(TOP_KS)


def prefetch(rag):
    """预先取满候选集（基于 MAX_TOP_K），避免网格循环中重复打网络。"""
    cache = []
    for qid, qtype, question, expects in QUESTIONS:
        plan = rag._plan(question)
        vector = list(rag.retriever.search(query_text=question, top_k=MAX_TOP_K * 4).items)
        by_name = rag._name_hits(question, labels=plan["labels"], limit=MAX_TOP_K * 2)
        expanded = rag._graph_expand(vector + by_name, plan, limit=MAX_TOP_K * 2)
        cache.append((qid, question, expects, vector, by_name, expanded))
    return cache


def main():
    ap = argparse.ArgumentParser(description="检索参数网格实验（带预取缓存）")
    ap.add_argument("--out", default=str(ROOT / "build_out" / "tune.json"))
    args = ap.parse_args()

    rag = PokemonGraphRAG()
    try:
        print(f"正在预取 {len(QUESTIONS)} 道题的候选池 (MAX_TOP_K={MAX_TOP_K})...")
        cached = prefetch(rag)
    finally:
        rag.close()

    print(f"预取完成，开始内存网格搜索 {len(TOP_KS)}×{len(RRF_KS)} 组合...")
    grid = []
    for top_k in TOP_KS:
        for rrf_k in RRF_KS:
            hits = 0
            for qid, question, expects, vector, by_name, expanded in cached:
                fused = rrf_fuse(
                    [vector[:top_k * 4], by_name[:top_k * 2], expanded[:top_k * 2]],
                    k=rrf_k)[:top_k]
                text = " ".join(it.content for it in fused)
                hits += all(e in text for e in expects)
            row = {"top_k": top_k, "rrf_k": rrf_k, "hit": hits, "total": len(QUESTIONS)}
            grid.append(row)
            print(f"  top_k={top_k:<3} RRF_K={rrf_k:<4} 命中 {hits}/{len(QUESTIONS)}")

    best = max(grid, key=lambda r: (r["hit"], -r["top_k"], -r["rrf_k"]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"grid": grid, "best": best},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n最佳参数组合: top_k={best['top_k']} RRF_K={best['rrf_k']} "
          f"命中 {best['hit']}/{best['total']} ({best['hit'] / best['total'] * 100:.1f}%)")
    print("已写出:", out)


if __name__ == "__main__":
    main()
