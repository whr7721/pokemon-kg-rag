import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from rag import PokemonGraphRAG

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

app = Flask(__name__)
CORS(app)
rag = PokemonGraphRAG()

@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/api/ask")
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question is required"}), 400
    top_k = int(data.get("top_k", 5))
    result = rag.ask(question, top_k=top_k)
    subgraph = rag.subgraph(result["evidence"], limit=3)
    return jsonify({
        "question": question,
        "answer": result["answer"],
        "evidence": result["evidence"],
        "subgraph": subgraph,
    })

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
