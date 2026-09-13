# -*- coding: utf-8 -*-
"""阶段二评测脚本：自动跑结构化直答 / 普通 RAG / GraphRAG 三路，输出 JSON + Markdown。

用法：
    python scripts/run_eval.py
输出：
    scripts/eval_results.json   原始结果
    scripts/eval_results.md     可读表格
"""
import json
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from router import RagRouter

# ──────────────────────────────────────────────
# 22 道评测题（覆盖 9 类）
# expected 字段用于后续确定性判分，第一版先收集答案
# ──────────────────────────────────────────────
QUESTIONS = [
    # 1. 实体属性（2）
    {"id": "q01", "category": "实体属性", "question": "妙蛙种子的属性是什么？",
     "expected": "草、毒"},
    {"id": "q02", "category": "实体属性", "question": "皮卡丘的分类是什么？",
     "expected": "鼠宝可梦"},

    # 2. 多跳进化（3）
    {"id": "q03", "category": "多跳进化", "question": "小火龙的最终进化是谁？",
     "expected": "喷火龙"},
    {"id": "q04", "category": "多跳进化", "question": "妙蛙种子多少级进化成妙蛙草？",
     "expected": "16级"},
    {"id": "q05", "category": "多跳进化", "question": "鲤鱼王的进化链是怎样的？",
     "expected": "鲤鱼王→暴鲤龙"},

    # 3. 招式筛选（3）
    {"id": "q06", "category": "招式筛选", "question": "哪些火属性招式威力大于80？",
     "expected": "喷射火焰、大字爆炎、烈焰冲锋等"},
    {"id": "q07", "category": "招式筛选", "question": "威力最高的电属性招式是什么？",
     "expected": "打雷（110）或电磁炮（120）"},
    {"id": "q08", "category": "招式筛选", "question": "哪些水系招式的命中不是100？",
     "expected": "水之波动、浊流等"},

    # 4. 双属性克制（3）
    {"id": "q09", "category": "双属性克制", "question": "草+毒属性被什么属性克制？",
     "expected": "火、冰、飞行、超能力"},
    {"id": "q10", "category": "双属性克制", "question": "火+飞行属性抵抗什么属性？",
     "expected": "草、虫、格斗、地面免疫、钢、火、妖精"},
    {"id": "q11", "category": "双属性克制", "question": "水+地面属性的弱点是什么？",
     "expected": "草"},

    # 5. 特性（2）
    {"id": "q12", "category": "特性", "question": "茂盛特性的效果是什么？",
     "expected": "HP低于1/3时草属性招式威力1.5倍"},
    {"id": "q13", "category": "特性", "question": "猛火特性在什么条件下触发？",
     "expected": "HP低于1/3时火属性招式威力1.5倍"},

    # 6. 蛋群（2）
    {"id": "q14", "category": "蛋群", "question": "妙蛙种子属于什么蛋群？",
     "expected": "怪兽、植物"},
    {"id": "q15", "category": "蛋群", "question": "哪些宝可梦同时属于怪兽和龙蛋群？",
     "expected": "列表（可能资料不足）"},

    # 7. 图鉴（2）
    {"id": "q16", "category": "图鉴", "question": "全国图鉴编号25的宝可梦是谁？",
     "expected": "皮卡丘"},
    {"id": "q17", "category": "图鉴", "question": "喷火龙的图鉴编号是多少？",
     "expected": "6"},

    # 8. 资料不足（2）
    {"id": "q18", "category": "资料不足", "question": "阿尔宙斯的属性是什么？",
     "expected": "一般（或资料不足）"},
    {"id": "q19", "category": "资料不足", "question": "超梦的隐藏特性是什么？",
     "expected": "压力（或资料不足）"},

    # 9. 比较类（3）
    {"id": "q20", "category": "比较类", "question": "喷火龙和水箭龟谁的特攻更高？",
     "expected": "喷火龙（109 vs 85）"},
    {"id": "q21", "category": "比较类", "question": "皮卡丘和雷丘谁的速度更快？",
     "expected": "雷丘（110 vs 90）"},
    {"id": "q22", "category": "比较类", "question": "火系和水系招式平均威力谁更高？",
     "expected": "火系"},
]


def run_one(router, item, top_k=5):
    """跑一道题的三路评测。"""
    q = item["question"]
    result = {
        "id": item["id"],
        "category": item["category"],
        "question": q,
        "expected": item["expected"],
    }

    # ── 1. 结构化直答 ──
    try:
        s = router.mqa.answer(q)
        if s is not None:
            result["structured"] = {
                "answer": s.get("answer", ""),
                "kind": s.get("kind", ""),
                "hit": True,
            }
        else:
            result["structured"] = {"answer": None, "kind": None, "hit": False}
    except Exception as e:
        result["structured"] = {"answer": f"ERROR: {e}", "kind": "error", "hit": False}

    # ── 2. 普通 RAG（use_graph=False）──
    try:
        n = router.rag.ask(q, top_k=top_k, use_graph=False)
        result["naive"] = {
            "answer": n.get("answer", ""),
            "evidence_count": len(n.get("evidence", [])),
            "facts_count": 0,
        }
    except Exception as e:
        result["naive"] = {"answer": f"ERROR: {e}", "evidence_count": 0, "facts_count": 0}

    # ── 3. GraphRAG（use_graph=True）──
    try:
        g = router.rag.ask(q, top_k=top_k, use_graph=True)
        result["graph"] = {
            "answer": g.get("answer", ""),
            "evidence_count": len(g.get("evidence", [])),
            "facts_count": len(g.get("facts", [])),
        }
    except Exception as e:
        result["graph"] = {"answer": f"ERROR: {e}", "evidence_count": 0, "facts_count": 0}

    return result


def to_md(results):
    """把结果转成 Markdown 表格。"""
    lines = [
        "# 阶段二评测结果（三路对照）",
        "",
        f"> 共 {len(results)} 题，自动跑结构化直答 / 普通 RAG / GraphRAG 三路",
        "",
        "| 编号 | 类别 | 问题 | 结构化直答 | 普通 RAG | GraphRAG | 图谱事实数 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        s_ans = (r["structured"]["answer"] or "未命中")[:40].replace("\n", " ")
        n_ans = (r["naive"]["answer"] or "")[:40].replace("\n", " ")
        g_ans = (r["graph"]["answer"] or "")[:40].replace("\n", " ")
        facts = r["graph"]["facts_count"]
        lines.append(
            f"| {r['id']} | {r['category']} | {r['question'][:25]} | {s_ans} | {n_ans} | {g_ans} | {facts} |"
        )

    # 按类别统计结构化命中率
    lines.append("\n## 结构化直答命中率\n")
    by_cat = {}
    for r in results:
        cat = r["category"]
        by_cat.setdefault(cat, [0, 0])
        by_cat[cat][1] += 1
        if r["structured"]["hit"]:
            by_cat[cat][0] += 1
    lines.append("| 类别 | 命中/总数 | 命中率 |")
    lines.append("|---|---|---|")
    for cat, (hit, total) in by_cat.items():
        lines.append(f"| {cat} | {hit}/{total} | {hit/total*100:.0f}% |")

    return "\n".join(lines)


def main():
    print("=" * 60)
    print("阶段二评测：结构化直答 vs 普通 RAG vs GraphRAG")
    print(f"共 {len(QUESTIONS)} 题")
    print("=" * 60)

    router = RagRouter()
    results = []
    t0 = time.time()

    for i, item in enumerate(QUESTIONS):
        print(f"\n[{i+1}/{len(QUESTIONS)}] {item['id']} 【{item['category']}】 {item['question']}")
        r = run_one(router, item)
        results.append(r)

        s_hit = "命中" if r["structured"]["hit"] else "未命中"
        s_ans = str(r["structured"]["answer"] or "")[:50].replace("\n", " ")
        n_ans = str(r["naive"]["answer"] or "")[:50].replace("\n", " ")
        g_ans = str(r["graph"]["answer"] or "")[:50].replace("\n", " ")
        print(f"  结构化[{s_hit}]: {s_ans}")
        print(f"  普通RAG:        {n_ans}")
        print(f"  GraphRAG:       {g_ans} (事实数:{r['graph']['facts_count']})")

    elapsed = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"全部完成，耗时 {elapsed:.1f} 秒")

    # 保存 JSON
    out_json = Path(__file__).parent / "eval_results.json"
    out_json.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"JSON: {out_json}")

    # 保存 Markdown
    out_md = Path(__file__).parent / "eval_results.md"
    out_md.write_text(to_md(results), encoding="utf-8")
    print(f"Markdown: {out_md}")

    router.close()


if __name__ == "__main__":
    main()
