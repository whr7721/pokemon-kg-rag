import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rag import PokemonGraphRAG

rag = PokemonGraphRAG()
items = rag.retrieve("皮卡丘是什么属性的宝可梦？", top_k=3)
print("=== RETRIEVED ===")
for it in items:
    meta = it["metadata"]
    text = it["text"].replace("\n", " ")
    print(f"[{meta['score']:.4f}] {meta['entity_label']}:{meta['entity_name']} | {text[:90]}")
rag.close()
print("smoke_rag done")
