from dotenv import load_dotenv
import os
from neo4j import GraphDatabase

load_dotenv()
driver = GraphDatabase.driver(
    os.getenv("NEO4J_URL"),
    auth=(os.getenv("NEO4J_USER"), os.getenv("NEO4J_PASSWORD")),
)

with driver.session(database=os.getenv("NEO4J_DB")) as session:
    print("=== NODES ===")
    for record in session.run("MATCH (n) RETURN labels(n)[0] AS label, count(n) AS c ORDER BY c DESC"):
        print(f"{record['label']:12} {record['c']}")

    print("=== RELATIONSHIPS ===")
    for record in session.run("MATCH ()-[r]->() RETURN type(r) AS rel, count(r) AS c ORDER BY c DESC"):
        print(f"{record['rel']:24} {record['c']}")

driver.close()
