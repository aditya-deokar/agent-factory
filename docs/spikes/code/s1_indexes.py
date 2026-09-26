"""Spike S1: vector + full-text indexes on Neo4j 5.26 (local Docker).

Creates both index kinds on a throwaway :SpikeSymbol label, queries them, and
cleans up. Vectors are tiny hand-made 4-d vectors; dimensions are what matter.

    uv run python docs/spikes/code/s1_indexes.py
"""

import os
import time

from dotenv import load_dotenv
from neo4j import GraphDatabase, RoutingControl

load_dotenv()
driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
)
db = os.environ.get("NEO4J_DATABASE", "neo4j")


def run(cypher, **p):
    return driver.execute_query(cypher, parameters_=p, database_=db)


print("server:", driver.get_server_info().agent)
run("MATCH (n:SpikeSymbol) DETACH DELETE n")
run(
    "CREATE VECTOR INDEX spike_vec IF NOT EXISTS FOR (n:SpikeSymbol) ON n.embedding "
    "OPTIONS {indexConfig: {`vector.dimensions`: 4, `vector.similarity_function`: 'cosine'}}"
)
run("CREATE FULLTEXT INDEX spike_ft IF NOT EXISTS FOR (n:SpikeSymbol) ON EACH [n.name, n.name_tokens]")
rows = [
    {"name": "TokenService", "tokens": "token service", "v": [1, 0, 0, 0]},
    {"name": "VerificationTokenService", "tokens": "verification token service", "v": [0.9, 0.1, 0, 0]},
    {"name": "EmailService", "tokens": "email service", "v": [0, 1, 0, 0]},
    {"name": "TeamRepository", "tokens": "team repository", "v": [0, 0, 1, 0]},
]
run(
    "UNWIND $rows AS r CREATE (:SpikeSymbol {name: r.name, name_tokens: r.tokens, embedding: r.v})",
    rows=rows,
)
run("CALL db.awaitIndexes(60)")

vec, _, _ = run(
    "CALL db.index.vector.queryNodes('spike_vec', 3, $q) YIELD node, score "
    "RETURN node.name AS name, round(score, 3) AS score",
    q=[1, 0.05, 0, 0],
)
print("vector top-3:", [(r["name"], r["score"]) for r in vec])

ft, _, _ = run(
    "CALL db.index.fulltext.queryNodes('spike_ft', 'token') YIELD node, score "
    "RETURN node.name AS name, round(score, 3) AS score"
)
print("fulltext 'token':", [(r["name"], r["score"]) for r in ft])

# EXPLAIN query_type: used by the Phase 5 Text2Cypher read-only guard.
for q in ["MATCH (n:SpikeSymbol) RETURN n LIMIT 1", "MATCH (n:SpikeSymbol) SET n.x = 1"]:
    summary = driver.execute_query("EXPLAIN " + q, database_=db, routing_=RoutingControl.READ).summary
    print(f"EXPLAIN query_type for {q!r}: {summary.query_type}")

run("MATCH (n:SpikeSymbol) DETACH DELETE n")
run("DROP INDEX spike_vec IF EXISTS")
run("DROP INDEX spike_ft IF EXISTS")
driver.close()
print("cleaned up")
