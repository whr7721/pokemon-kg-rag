import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag import PokemonGraphRAG

questions = [
    "皮卡丘是什么属性的宝可梦？",
    "妙蛙种子最终进化成什么？",
]

rag = PokemonGraphRAG()
for q in questions:
    print("Q:", q)
    result = rag.ask(q, top_k=5)
    print("A:", result["answer"])
    print("证据数:", len(result["evidence"]))
    for e in result["evidence"][:3]:
        print("  -", e["metadata"]["entity_name"], "|", e["text"].replace("\n", " ")[:70])
    print("-" * 40)
rag.close()
print("smoke_ask done")
