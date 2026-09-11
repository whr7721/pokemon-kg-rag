# -*- coding: utf-8 -*-
"""Flask 入口（阶段一最终合并版）。

/api/ask 策略：
    1. MultiQA 结构化路由命中（进化/克制/关系/特性/策略/建议）时直接返回；
    2. 未命中回落 PokemonGraphRAG：默认注入图谱邻域事实（GraphRAG）；
       前端可传 use_graph=false 运行纯文本 RAG 作为对照。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from flask_cors import CORS

from rag import as_bool
from router import RagRouter

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = Flask(__name__)
CORS(app)
router = RagRouter()


@app.get("/api/health")
def health():
    snapshot = router.health()
    return jsonify({"status": "ok" if snapshot["ok"] else "degraded", **snapshot})


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/test")
def test():
    return render_template("tester.html")


@app.post("/api/ask")
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    top_k = int(data.get("top_k", 8))
    use_graph = as_bool(data.get("use_graph", True))

    return jsonify(router.answer(question, top_k=top_k, use_graph=use_graph))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
