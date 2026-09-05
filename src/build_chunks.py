import os
from dotenv import load_dotenv
from neo4j import GraphDatabase
import data_loader as dl
import chunker
from embedder import BgeM3Embedder

load_dotenv()

def get_driver():
    return GraphDatabase.driver(
        os.getenv("NEO4J_URL"),
        auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
    )

def import_chunks(tx, rows):
    for start in range(0, len(rows), 1000):
        tx.run(
            """
            UNWIND $rows AS row
            CREATE (c:Chunk {chunk_id: row.chunk_id})
            SET c.text = row.text,
                c.kind = row.kind,
                c.entity_type = row.entity_type,
                c.entity_id = row.entity_id,
                c.embedding = row.embedding
            """,
            rows=rows[start:start + 1000],
        )

def build_chunks():
    chunks = []
    for path in dl.pokemon_files():
        chunks.extend(chunker.pokemon_chunks(dl.load_json(path)))
    for m in dl.load_move_list():
        chunks.append(chunker.move_chunk(m))
    for a in dl.load_ability_list():
        chunks.append(chunker.ability_chunk(a))
    chunks = [c for c in chunks if c["text"].strip()]
    print("chunks to embed:", len(chunks))

    embedder = BgeM3Embedder()
    vectors = embedder.embed_texts([c["text"] for c in chunks])
    for c, v in zip(chunks, vectors):
        c["embedding"] = v

    driver = get_driver()
    db = os.getenv("NEO4J_DB")
    with driver.session(database=db) as session:
        session.run("MATCH (c:Chunk) DETACH DELETE c")
        session.execute_write(import_chunks, chunks)
        session.run(
            "MATCH (c:Chunk {entity_type:'Pokemon'}) "
            "MATCH (p:Pokemon {pokedex_id: c.entity_id}) "
            "MERGE (c)-[:DESCRIBES]->(p)"
        )
        session.run(
            "MATCH (c:Chunk {entity_type:'Move'}) "
            "MATCH (m:Move {name_zh: c.entity_id}) "
            "MERGE (c)-[:DESCRIBES]->(m)"
        )
        session.run(
            "MATCH (c:Chunk {entity_type:'Ability'}) "
            "MATCH (a:Ability {name_zh: c.entity_id}) "
            "MERGE (c)-[:DESCRIBES]->(a)"
        )
        print("--- CHUNK KINDS ---")
        for r in session.run("MATCH (c:Chunk) RETURN c.kind AS kind, count(c) AS n ORDER BY kind"):
            print(r["kind"], r["n"])
    driver.close()
    print("build_chunks done")

if __name__ == "__main__":
    build_chunks()
