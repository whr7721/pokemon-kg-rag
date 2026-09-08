# -*- coding: utf-8 -*-
"""计数/对拍，经仓库内 common.neo_http。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import neo_http  # noqa: E402

def main():
    for label, rows in [("nodes", neo_http.query("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c ORDER BY c DESC")),
                        ("rels", neo_http.query("MATCH ()-[x]->() RETURN type(x) AS t, count(x) AS c ORDER BY c DESC"))]:
        print("==", label.upper(), "==")
        for r in rows:
            print(f"{r[0]:24} {r[1]}")

if __name__ == "__main__":
    main()
