# -*- coding: utf-8 -*-
"""索引：向量/唯一约束，经仓库内 common.neo_http。"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common import neo_http  # noqa: E402

STEPS = [
    "CREATE VECTOR INDEX embedding_Chunk IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}",
    "CREATE CONSTRAINT pokemon_id IF NOT EXISTS FOR (p:Pokemon) REQUIRE p.pokedex_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
]

def main():
    for s in STEPS:
        try:
            neo_http.query(s)
            print("OK:", s[:70])
        except Exception as e:
            print("跳过:", str(e)[:100])

if __name__ == "__main__":
    main()
