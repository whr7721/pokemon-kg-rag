# -*- coding: utf-8 -*-
"""索引：向量/唯一约束。

注意：Aura 的 HTTP Query API 对 DDL 会返回成功但不生效，因此这里改用
neo4j 官方驱动（Bolt）执行，与 src/rag.py 的读取通道一致。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402
from neo4j import GraphDatabase  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

STEPS = [
    "DROP INDEX chunk_embedding IF EXISTS",
    "CREATE FULLTEXT INDEX pokemonFulltext IF NOT EXISTS FOR (p:Pokemon) ON EACH [p.name_zh, p.name_ja, p.name_en, p.text] OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}",
    "CREATE FULLTEXT INDEX abilityFulltext IF NOT EXISTS FOR (a:Ability) ON EACH [a.name_zh, a.effect, a.text] OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}",
    "CREATE FULLTEXT INDEX moveFulltext IF NOT EXISTS FOR (m:Move) ON EACH [m.name_zh, m.description, m.text] OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}",
    "CREATE FULLTEXT INDEX formFulltext IF NOT EXISTS FOR (f:Form) ON EACH [f.form_name, f.text] OPTIONS {indexConfig: {`fulltext.analyzer`: 'cjk'}}",
    "CREATE VECTOR INDEX embedding_Chunk IF NOT EXISTS FOR (c:Chunk) ON (c.embedding) OPTIONS {indexConfig: {`vector.dimensions`: 1024, `vector.similarity_function`: 'cosine'}}",
    "CREATE CONSTRAINT pokemon_id IF NOT EXISTS FOR (p:Pokemon) REQUIRE p.pokedex_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT Pokemon_id IF NOT EXISTS FOR (p:Pokemon) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT Form_id IF NOT EXISTS FOR (f:Form) REQUIRE f.id IS UNIQUE",
    "CREATE CONSTRAINT Move_id IF NOT EXISTS FOR (m:Move) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT Ability_id IF NOT EXISTS FOR (a:Ability) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT Type_id IF NOT EXISTS FOR (t:Type) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT EggGroup_id IF NOT EXISTS FOR (e:EggGroup) REQUIRE e.id IS UNIQUE",
    "CREATE CONSTRAINT RegionDex_id IF NOT EXISTS FOR (r:RegionDex) REQUIRE r.id IS UNIQUE",
]


def main():
    driver = GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
    )
    db = os.getenv("NEO4J_DB", "neo4j")
    try:
        with driver.session(database=db) as session:
            for s in STEPS:
                session.run(s).consume()
                print("OK:", s[:70])
            names = [r["name"] for r in session.run(
                "SHOW INDEXES YIELD name WHERE name IN $names RETURN name",
                names=[
                    "pokemonFulltext", "abilityFulltext", "moveFulltext",
                    "formFulltext", "embedding_Chunk", "pokemon_id", "chunk_id",
                    "Pokemon_id", "Form_id", "Move_id", "Ability_id",
                    "Type_id", "EggGroup_id", "RegionDex_id",
                ],
            )]
        print("已确认索引/约束:", ", ".join(names))
    finally:
        driver.close()


if __name__ == "__main__":
    main()
