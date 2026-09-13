# -*- coding: utf-8 -*-
"""检索层 ablation：仅向量 / 仅全文 / hybrid / hybrid+图谱扩展。

四路都只走检索、不调用大模型，判分是「期望子串是否出现在召回证据里」，
衡量的是检索召回率，不是生成质量；资料不足类问题属生成层评测，不在此列。
向量与全文通道对每题只跑一次，四路从同一批结果派生，保证对比公平。

用法（仓库根）：
    python scripts/run_ablation.py --out build_out/ablation.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from rag import PokemonGraphRAG, rrf_fuse  # noqa: E402

MODES = ("vector", "fulltext", "hybrid", "hybrid_graph")

# (编号, 类型, 问题, 期望子串)；前 14 条取自 scripts/smoke_multiqa.py 金标，
# 后 5 条取自 docs/evaluation.md 阶段一评测题。
QUESTIONS = [
    ("q01", "evolution", "妙蛙种子最终进化成什么？", ["妙蛙花"]),
    ("q02", "evolution", "耿鬼是怎么进化出来的？", ["鬼斯通", "连接交换"]),
    ("q03", "evolution", "伊布可以进化成哪些宝可梦？条件分别是什么？", ["水伊布", "水之石"]),
    ("q04", "counter", "什么宝可梦克制阿柏怪？", ["地面", "超能力"]),
    ("q05", "counter", "皮卡丘怕什么属性？", ["地面"]),
    ("q06", "counter", "什么宝可梦能克制妙蛙种子？", ["飞行", "火"]),
    ("q07", "counter", "烈咬陆鲨被什么4倍克制？", ["冰"]),
    ("q08", "ability", "拥有避雷针特性的宝可梦有哪些？", ["皮卡丘", "雷丘"]),
    ("q09", "ability", "拥有茂盛特性的宝可梦有哪些？", ["妙蛙种子", "妙蛙花"]),
    ("q10", "ability", "拥有悬浮特性的宝可梦有哪些？", ["飘浮"]),
    ("q11", "suggest", "拥有蓄水特性的宝可梦有哪些？", ["储水", "引水"]),
    ("q12", "counter", "板匙蛇怕什么属性？", ["地面"]),
    ("q13", "strategy", "皮卡丘应对电系宝可梦时，适合携带什么特性？", ["避雷针"]),
    ("q14", "strategy", "电击魔兽应对电系宝可梦时，适合携带什么特性？", ["电气引擎"]),
    ("q15", "属性", "妙蛙种子的属性是什么？", ["草", "毒"]),
    ("q16", "进化", "小火龙的最终进化是谁？", ["喷火龙"]),
    ("q17", "招式", "哪些火属性招式威力大于 80？", ["大字爆炎"]),
    ("q18", "属性克制", "草属性克制哪些属性？", ["地面", "岩石"]),
    ("q19", "特性", "茂盛特性的效果是什么？", ["1.5"]),
]


def evaluate(rag, top_k=8, verbose=True):
    """返回 (rows, summary)。"""
    rows = []
    summary = {mode: 0 for mode in MODES}
    for qid, qtype, question, expects in QUESTIONS:
        plan = rag._plan(question)
        vector = list(rag.retriever.search(query_text=question, top_k=top_k * 4).items)
        by_name = rag._name_hits(question, labels=plan["labels"], limit=top_k * 2)
        expanded = rag._graph_expand(vector + by_name, plan, limit=top_k * 2)
        batches = {
            "vector": vector[:top_k],
            "fulltext": by_name[:top_k],
            "hybrid": rrf_fuse([vector, by_name], k=rag.rrf_k)[:top_k],
            "hybrid_graph": rrf_fuse([vector, by_name, expanded], k=rag.rrf_k)[:top_k],
        }
        result = {}
        for mode, items in batches.items():
            text = " ".join(it.content for it in items)
            ok = all(e in text for e in expects)
            summary[mode] += ok
            result[mode] = {
                "hit": ok,
                "entities": [it.metadata.get("entity_name") for it in items],
            }
        rows.append({"id": qid, "type": qtype, "question": question,
                     "expects": expects, "results": result})
        if verbose:
            print(f"{qid} {' '.join(m for m in MODES if result[m]['hit']):<48} {question}")
    return rows, summary


def to_markdown(summary, rows, top_k):
    total = len(QUESTIONS)
    lines = [
        f"# 检索 ablation（top_k={top_k}，题量 {total}）",
        "",
        "判分：期望子串全部出现在该路召回证据的文本拼接中记为命中；只衡量检索召回，不含生成。",
        "",
        "| 路径 | 命中 | 命中率 |",
        "| --- | --- | --- |",
    ]
    for mode in MODES:
        lines.append(f"| `{mode}` | {summary[mode]}/{total} | {summary[mode] / total * 100:.0f}% |")
    lines += ["", "| 题号 | 类型 | 问题 | " + " | ".join(MODES) + " |",
              "| --- | --- | --- | " + " | ".join("---" for _ in MODES) + " |"]
    for row in rows:
        marks = ["✓" if row["results"][m]["hit"] else "✗" for m in MODES]
        lines.append(f"| {row['id']} | {row['type']} | {row['question']} | "
                     + " | ".join(marks) + " |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser(description="检索层 ablation 四路对比")
    ap.add_argument("--out", default=str(ROOT / "build_out" / "ablation.json"))
    ap.add_argument("--top-k", type=int, default=8)
    args = ap.parse_args()

    rag = PokemonGraphRAG()
    try:
        rows, summary = evaluate(rag, top_k=args.top_k)
    finally:
        rag.close()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(
        {"top_k": args.top_k, "total": len(QUESTIONS),
         "summary": summary, "rows": rows}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    md_path = out.with_suffix(".md")
    md_path.write_text(to_markdown(summary, rows, args.top_k), encoding="utf-8")

    print("\n" + "-" * 40)
    for mode in MODES:
        print(f"  {mode:14} {summary[mode]}/{len(QUESTIONS)}")
    print("已写出:", out, "和", md_path)


if __name__ == "__main__":
    main()
