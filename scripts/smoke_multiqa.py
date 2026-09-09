#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多格式问答金标冒烟：进化 / 克制(含双属性) / 特性 / 策略。开放题回落 GraphRAG。

叙事关系题暂不参与金标：当前 build_engine 未生成 RIVAL_OF/PREDATES_ON 等边。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from multi_qa import MultiQA, classify  # noqa: E402

GOLDEN = [
    # (类别, 问题, [期望子串])
    ("evolution", "妙蛙种子最终进化成什么？", ["妙蛙花"]),
    ("evolution", "耿鬼是怎么进化出来的？", ["鬼斯通", "连接交换", "鬼斯"]),
    ("evolution", "伊布可以进化成哪些宝可梦？条件分别是什么？", ["水伊布", "水之石"]),
    ("counter", "什么宝可梦克制阿柏怪？", ["地面", "超能力"]),
    ("counter", "皮卡丘怕什么属性？", ["地面"]),
    ("counter", "什么宝可梦能克制妙蛙种子？", ["飞行", "火"]),
    ("counter", "烈咬陆鲨被什么4倍克制？", ["冰"]),
    ("ability", "拥有避雷针特性的宝可梦有哪些？", ["皮卡丘", "雷丘", "嘎啦嘎啦"]),
    ("ability", "拥有茂盛特性的宝可梦有哪些？", ["妙蛙种子", "妙蛙花"]),
    ("ability", "拥有悬浮特性的宝可梦有哪些？", ["飘浮"]),
    ("suggest", "拥有蓄水特性的宝可梦有哪些？", ["储水", "引水"]),
    ("counter", "板匙蛇怕什么属性？", ["地面"]),
    ("strategy", "皮卡丘应对电系宝可梦时，适合携带什么特性？", ["避雷针", "静电"]),
    ("strategy", "电击魔兽应对电系宝可梦时，适合携带什么特性？", ["电气引擎"]),
    # ("relation", "饭匙蛇和猫鼬斩是什么关系？", ["宿敌"]),  # 叙事边未纳入 build_engine，暂不参与金标
    # ("relation", "饭匙蛇讨厌什么宝可梦？", ["猫鼬斩"]),
    # ("relation", "谁教导凯路迪欧？", ["勾帕路翁"]),
    # ("relation", "谁捕食铁蚁？", ["熔蚁兽"]),
]


def main():
    mqa = MultiQA()
    passed = 0
    for kind, q, expects in GOLDEN:
        res = mqa.answer(q)
        answer = res["answer"] if res else "[open 回落]"
        ok = res is not None and res["kind"] == kind and all(e in answer for e in expects)
        passed += ok
        print(("✅" if ok else "❌"), f"[{kind}] {q}")
        print("   →", answer.replace("\n", "\n    "))
        if not ok:
            print("   !!! 期望包含:", expects, "| 实际 kind:", res["kind"] if res else None)
    print("-" * 40)
    print(f"通过 {passed}/{len(GOLDEN)}")
    mqa.close()
    return 0 if passed == len(GOLDEN) else 1


if __name__ == "__main__":
    sys.exit(main())
