# -*- coding: utf-8 -*-
"""阶段二评测判分脚本：确定性关键词匹配 + 图查询验证双重判分。

用法：
    python scripts/score_eval.py
输出：
    scripts/eval_scores.json   带评分的完整结果
    scripts/eval_scores.md     可读评分表
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from graph_access import GraphAccess

RESULT_FILE = Path(__file__).parent / "eval_results.json"
OUT_JSON = Path(__file__).parent / "eval_scores.json"
OUT_MD = Path(__file__).parent / "eval_scores.md"

# ──────────────────────────────────────────────
# 判分规则（0-5分）
#   5 完全正确，覆盖全部要点
#   4 正确，有小瑕疵
#   3 部分正确，核心对但不完整
#   2 少量正确，大部分错误
#   1 错误但无幻觉
#   0 资料不足（应有答案时）或严重幻觉
# ──────────────────────────────────────────────

def is_insufficient(answer):
    if not answer:
        return True
    a = str(answer).strip()
    return ("资料不足" in a or "无法回答" in a or a.startswith("ERROR"))


def keyword_score(answer, expected_keywords):
    """基于 expected 关键词匹配给分。"""
    if not expected_keywords:
        return None  # 无法用关键词判分，返回 None 待人工/LLM
    ans = str(answer)
    hit = sum(1 for kw in expected_keywords if kw in ans)
    total = len(expected_keywords)
    if hit == total:
        return 5
    if hit >= total * 0.6:
        return 4
    if hit >= total * 0.3:
        return 3
    if hit > 0:
        return 2
    return 1


# 每道题的判分关键词（从 expected 提取，人工补充）
QUESTION_KEYWORDS = {
    "q01": ["草", "毒"],
    "q02": ["鼠"],  # 鼠宝可梦
    "q03": ["喷火龙"],
    "q04": ["16"],
    "q05": ["暴鲤龙", "20"],
    "q06": ["V热焰", "喷射火焰", "大字爆", "烈焰"],  # 火属性高威力招式
    "q07": ["打雷", "电磁炮", "闪电", "100", "110", "120"],
    "q08": ["水之波动", "浊流", "喷水", "热水"],
    "q09": ["火", "冰", "飞行", "超能力"],  # 草+毒被克制：火、冰、飞行、超能力（毒抵消了草的毒弱点）
    "q10": ["格斗", "虫", "草"],  # 火+飞行抵抗
    "q11": ["草"],  # 水+地面弱点只有草
    "q12": ["1/3", "1⁄3", "1.5", "1.5倍"],
    "q13": ["1/3", "1⁄3", "1.5", "1.5倍"],
    "q14": ["怪兽", "植物"],
    "q15": ["资料不足"],  # 这题预期就是资料不足或列表
    "q16": ["皮卡丘"],
    "q17": ["6"],
    "q18": ["一般"],
    "q19": ["紧张感"],
    "q20": ["资料不足"],  # 比较类预期资料不足
    "q21": ["资料不足"],
    "q22": ["资料不足"],
}

# 图查询验证（对部分题直接查图拿标准答案）
def verify_by_graph(graph, item):
    """用图查询验证答案，返回 (标准答案描述, 判分提示)。"""
    qid = item["id"]
    try:
        if qid == "q01":
            rows = graph.run("""
                MATCH (p:Pokemon {name_zh: '妙蛙种子'})-[:HAS_FORM]->(:Form)-[:HAS_TYPE]->(t:Type)
                RETURN collect(DISTINCT t.name_zh) AS types
            """)
            return f"图查标准答案: {rows[0]['types']}", None
        if qid == "q03":
            rows = graph.run("""
                MATCH (p:Pokemon {name_zh: '小火龙'})
                OPTIONAL MATCH (p)-[:EVOLVES_TO*]->(final:Pokemon)
                WHERE NOT (final)-[:EVOLVES_TO]->()
                RETURN collect(DISTINCT final.name_zh) AS finals
            """)
            return f"图查标准答案: {rows[0]['finals']}", None
        if qid == "q17":
            rows = graph.run("MATCH (p:Pokemon {name_zh: '喷火龙'}) RETURN p.pokedex_id AS id")
            return f"图查标准答案: 编号{rows[0]['id']}", None
        if qid == "q16":
            rows = graph.run("MATCH (p:Pokemon {pokedex_id: 25}) RETURN p.name_zh AS name")
            return f"图查标准答案: {rows[0]['name']}", None
    except Exception as e:
        return None, f"图查询出错: {e}"
    return None, None


def score_one(answer, expected_keywords, category, qid):
    """给单路答案判分，返回 (score, reason)。"""
    if is_insufficient(answer):
        # 资料不足题（q15/q20/q21/q22）说资料不足算正确
        if qid in ("q15", "q20", "q21", "q22"):
            return 5, "正确回答资料不足"
        return 0, "资料不足（应有答案）"

    if expected_keywords is None:
        return None, "无关键词规则，待 LLM/人工判分"

    kw_score = keyword_score(answer, expected_keywords)
    if kw_score == 5:
        return 5, f"关键词全部命中 ({'/'.join(expected_keywords)})"
    if kw_score == 4:
        return 4, "关键词大部分命中"
    if kw_score == 3:
        return 3, "关键词部分命中"
    if kw_score == 2:
        return 2, "仅少量关键词命中"
    return 1, "关键词未命中，可能错误"


def main():
    results = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
    graph = GraphAccess()

    print("=" * 60)
    print("阶段二评测判分：确定性关键词 + 图查询验证")
    print(f"共 {len(results)} 题 × 3 路")
    print("=" * 60)

    scored = []
    for item in results:
        qid = item["id"]
        kws = QUESTION_KEYWORDS.get(qid)
        cat = item["category"]

        # 图查询验证（部分题）
        graph_truth, graph_err = verify_by_graph(graph, item)
        if graph_truth:
            print(f"\n[{qid}] {graph_truth}")

        row = {"id": qid, "category": cat, "question": item["question"],
               "expected": item["expected"], "graph_truth": graph_truth}

        for mode in ["structured", "naive", "graph"]:
            ans = item[mode].get("answer")
            score, reason = score_one(ans, kws, cat, qid)
            row[mode] = {
                "answer": ans,
                "score": score,
                "reason": reason,
                "hit": item[mode].get("hit"),
                "facts_count": item[mode].get("facts_count", 0),
                "evidence_count": item[mode].get("evidence_count", 0),
            }
            s_str = f"{score}/5" if score is not None else "待评"
            print(f"  {mode:12s}: {s_str} - {reason} | {str(ans or '')[:40]}")

        scored.append(row)

    graph.close()

    # ── 统计 ──
    print("\n" + "=" * 60)
    print("汇总统计")
    print("=" * 60)
    for mode in ["structured", "naive", "graph"]:
        scores = [r[mode]["score"] for r in scored if r[mode]["score"] is not None]
        if scores:
            avg = sum(scores) / len(scores)
            full = sum(1 for s in scores if s >= 4)
            print(f"  {mode:12s}: 平均分 {avg:.2f}/5 | 答对(≥4) {full}/{len(scores)} | 待评 {len(scored)-len(scores)}")

    # ── 保存 JSON ──
    OUT_JSON.write_text(json.dumps(scored, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nJSON: {OUT_JSON}")

    # ── 保存 Markdown ──
    lines = [
        "# 阶段二评测评分表（确定性判分）",
        "",
        "> 判分方式：关键词匹配 + 图查询验证；0-5分制；待评项需 LLM/人工补充",
        "",
        "| 编号 | 类别 | 问题 | 结构化直答 | 普通 RAG | GraphRAG | 图查标准答案 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in scored:
        def cell(m):
            s = r[m]["score"]
            s_str = f"**{s}/5**" if s is not None else "待评"
            ans = str(r[m]["answer"] or "")[:30].replace("\n", " ")
            return f"{s_str}<br/>{ans}"
        truth = (r.get("graph_truth") or "")[:30]
        lines.append(
            f"| {r['id']} | {r['category']} | {r['question'][:20]} | "
            f"{cell('structured')} | {cell('naive')} | {cell('graph')} | {truth} |"
        )

    # 按类别统计
    lines.append("\n## 按类别平均分\n")
    lines.append("| 类别 | 题数 | 结构化直答 | 普通 RAG | GraphRAG |")
    lines.append("|---|---|---|---|---|")
    by_cat = {}
    for r in scored:
        by_cat.setdefault(r["category"], []).append(r)
    for cat, rows in by_cat.items():
        def avg(mode):
            ss = [r[mode]["score"] for r in rows if r[mode]["score"] is not None]
            return f"{sum(ss)/len(ss):.2f}" if ss else "-"
        lines.append(f"| {cat} | {len(rows)} | {avg('structured')} | {avg('naive')} | {avg('graph')} |")

    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown: {OUT_MD}")


if __name__ == "__main__":
    main()
