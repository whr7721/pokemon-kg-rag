# -*- coding: utf-8 -*-
"""实体/关系建图（引擎内置在 src/build_engine.py）。"""
from __future__ import annotations
import argparse, json, runpy, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "src" / "build_engine.py"

def build(dataset, out, no_learn_text=False):
    if not ENGINE.exists():
        raise RuntimeError(f"缺少内置引擎 {ENGINE}")
    argv = ["build_engine.py", "--dataset", dataset, "--out", out]
    if no_learn_text:
        argv.append("--no-learn-text")
    sys.argv[:] = argv
    runpy.run_path(str(ENGINE), run_name="__main__")
    report = Path(out) / "build_report.json"
    if report.exists():
        r = json.loads(report.read_text(encoding="utf-8"))
        print("实体:", r.get("entities_total"), "| 关系:", r.get("relations_total"),
              "| 克制全表:", r.get("type_chart_edges"))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--out", default=str(ROOT / "build_out"))
    ap.add_argument("--no-learn-text", action="store_true")
    args = ap.parse_args()
    build(args.dataset, args.out, args.no_learn_text)

if __name__ == "__main__":
    main()
