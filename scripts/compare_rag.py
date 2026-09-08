import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from rag import PokemonGraphRAG

questions = [
    {
        "id": "q1",
        "type": "实体属性",
        "question": "妙蛙种子的属性是什么？",
    },
    {
        "id": "q2",
        "type": "进化/多跳",
        "question": "小火龙的最终进化是谁？",
    },
    {
        "id": "q3",
        "type": "招式检索",
        "question": "哪些火属性招式威力大于 80？",
    },
    {
        "id": "q4",
        "type": "属性克制",
        "question": "草属性克制哪些属性？",
    },
    {
        "id": "q5",
        "type": "特性效果",
        "question": "茂盛特性的效果是什么？",
    },
]

rag = PokemonGraphRAG()

print("=" * 60)
print("成员三：普通 RAG (纯文本) vs GraphRAG (图谱事实注入) 对照实验")
print("=" * 60)

results = []
for item in questions:
    q = item["question"]
    qid = item["id"]
    qtype = item["type"]
    print(f"\n### 实验 [{qid}] 【{qtype}】: {q}\n")

    # 1. 纯文本 Naive RAG
    naive_res = rag.ask(q, top_k=5, use_graph=False)
    # 2. GraphRAG
    graph_res = rag.ask(q, top_k=5, use_graph=True)

    print("--- [纯文本 RAG] ---")
    print("回答:", naive_res["answer"])
    print("证据实体:", [e["metadata"]["entity_name"] for e in naive_res["evidence"]])

    print("\n--- [GraphRAG (文本 + 图谱事实)] ---")
    print("回答:", graph_res["answer"])
    print("注入事实数:", len(graph_res.get("facts", [])))
    for f in graph_res.get("facts", []):
        d = f.get("data") or {}
        print(f"  * {f['label']}: {d.get('name')}")

    results.append({
        "item": item,
        "naive": naive_res,
        "graph": graph_res,
    })

rag.close()
print("\n对照实验全部完成。")
