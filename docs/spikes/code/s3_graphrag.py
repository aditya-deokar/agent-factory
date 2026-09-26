"""Spike S3: neo4j-graphrag VectorCypherRetriever with a custom index name,
custom retrieval query, and query_params (project scoping). Offline embedder.

    uv run python docs/spikes/code/s3_graphrag.py
"""

import hashlib
import math
import os
import re

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j_graphrag.embeddings.base import Embedder
from neo4j_graphrag.retrievers import VectorCypherRetriever

load_dotenv()
URI = os.environ["NEO4J_URI"]
assert "localhost" in URI, "S3 writes data: local Neo4j only"
driver = GraphDatabase.driver(URI, auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
DIM = 64


class HashEmbedder(Embedder):
    def embed_query(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for tok in re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()):
            v[int(hashlib.sha256(tok.encode()).hexdigest(), 16) % DIM] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1
        return [x / n for x in v]


emb = HashEmbedder()
q = driver.execute_query
q("MATCH (n:S3Sym) DETACH DELETE n")
q(f"CREATE VECTOR INDEX s3_vec IF NOT EXISTS FOR (n:S3Sym) ON n.embedding "
  f"OPTIONS {{indexConfig: {{`vector.dimensions`: {DIM}, `vector.similarity_function`: 'cosine'}}}}")
cards = {
    ("p1", "TokenService"): "TokenService create validate consume expire token",
    ("p1", "PasswordResetService"): "PasswordResetService reset password token email",
    ("p1", "EmailService"): "EmailService send email template",
    ("p2", "TokenService"): "TokenService create validate token other project",
}
q("UNWIND $rows AS r CREATE (:S3Sym {project_id: r.p, name: r.n, card: r.c, embedding: r.e})",
  rows=[{"p": p, "n": n, "c": c, "e": emb.embed_query(c)} for (p, n), c in cards.items()])
q("MATCH (a:S3Sym {name:'PasswordResetService', project_id:'p1'}), (b:S3Sym {name:'TokenService', project_id:'p1'}) "
  "CREATE (a)-[:USES]->(b)")
q("CALL db.awaitIndexes(60)")

retriever = VectorCypherRetriever(
    driver,
    index_name="s3_vec",
    embedder=emb,
    retrieval_query=(
        "WITH node, score WHERE node.project_id = $project_id "
        "OPTIONAL MATCH (node)<-[:USES]-(u) "
        "RETURN node.name AS name, score, count(u) AS fan_in"
    ),
)
res = retriever.search(query_text="invitation token create validate", top_k=4, query_params={"project_id": "p1"})
for item in res.items:
    print(item.content)

q("MATCH (n:S3Sym) DETACH DELETE n")
q("DROP INDEX s3_vec IF EXISTS")
driver.close()
