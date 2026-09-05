from dotenv import load_dotenv
import os
from neo4j import GraphDatabase
from neo4j_graphrag.indexes import create_vector_index

load_dotenv()

driver = GraphDatabase.driver(
    os.getenv("NEO4J_URL"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
)
db = os.getenv("NEO4J_DB")

with driver.session(database=db) as session:
    print("=== CHUNKS ===")
    for r in session.run("MATCH (c:Chunk) RETURN c.kind AS kind, count(c) AS n ORDER BY kind"):
        print(f"{r['kind']:14} {r['n']}")
    print("=== DESCRIBES ===")
    for r in session.run("MATCH (c:Chunk)-[r:DESCRIBES]->(e) RETURN labels(e)[0] AS label, count(r) AS n ORDER BY n DESC"):
        print(f"{r['label']:14} {r['n']}")
    print("=== EMBEDDING DIM ===")
    for r in session.run("MATCH (c:Chunk) RETURN min(size(c.embedding)) AS lo, max(size(c.embedding)) AS hi"):
        print(f"min={r['lo']} max={r['hi']}")

create_vector_index(driver, "chunk_embedding", "Chunk", "embedding", 1024, "cosine", neo4j_database=db)
print("vector index created")

with driver.session(database=db) as session:
    session.run("CREATE FULLTEXT INDEX chunk_text IF NOT EXISTS FOR (c:Chunk) ON EACH [c.text]")
    print("fulltext index created")
    print("=== INDEXES ===")
    for r in session.run("SHOW INDEXES YIELD name, type, state WHERE name IN ['chunk_embedding','chunk_text'] RETURN name, type, state"):
        print(f"{r['name']} {r['type']} {r['state']}")

driver.close()
print("setup_indexes done")
