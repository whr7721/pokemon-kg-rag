# -*- coding: utf-8 -*-
"""架构自检：确认 MultiQA / GraphRAG / app 不再各自创建 Neo4j driver。"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def tree(name):
    return ast.parse((SRC / name).read_text(encoding="utf-8"))


def imported_names(name):
    out = []
    for node in ast.walk(tree(name)):
        if isinstance(node, ast.ImportFrom) and node.module == "neo4j":
            out.extend(a.name for a in node.names)
    return out


def function_names(name):
    return [n.name for n in ast.walk(tree(name)) if isinstance(n, ast.FunctionDef)]


assert "GraphDatabase" not in imported_names("multi_qa.py")
assert "GraphDatabase" not in imported_names("rag.py")
assert "GraphDatabase" not in imported_names("app.py")
assert "get_driver" not in function_names("multi_qa.py")
assert "get_driver" not in function_names("rag.py")
assert "get_driver" in function_names("graph_access.py")

from router import RagRouter

router = RagRouter()
assert router.graph is router.mqa.graph
assert router.graph is router.rag.graph
router.close()
print("architecture ok")
