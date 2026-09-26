"""`agent-factory ask`: natural-language questions about the code graph via Text2Cypher, read-only by force.

neo4j-graphrag's Text2CypherTemplate + LLM generate the Cypher; Agent Factory executes it itself behind
four guards (the stock retriever would execute in a write-capable session):
  1 static reject list for write clauses and procedures
  2 EXPLAIN: the server-side query type must be 'r' (read-only)
  3 execution in a READ transaction with a timeout
  4 a LIMIT appended when missing
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..db.stores import Neo4jStore

_WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|FOREACH|LOAD\s+CSV)\b|"
    r"CALL\s+apoc\.(?!meta\.)|CALL\s+db\.(create|drop|index\.fulltext\.create)|CALL\s+dbms\.|"
    r"\bIN\s+TRANSACTIONS\b",
    re.IGNORECASE,
)
_FENCE = re.compile(r"^```(?:cypher)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
MAX_ROWS = 100

SCHEMA = """Node labels and key properties (every node has project_id; always filter on project_id = $project_id):
- Symbol {uid, name, qualname, kind: class|function|method|route|const|interface|type, path, line_start, roles: list,
  http_method, http_path, table}. Extra labels by role: Service, Repository, Controller, Route, Model, Validator,
  Middleware, Integration, Component, Hook, Worker.
- File {uid, path, lang} (tests also have label TestFile); Module {uid, path}
- Knowledge {uid, kind: pattern|decision|constraint, title, claim,
  status: candidate|validated|deprecated|superseded|rejected, confidence, support_count, violation_count}
  with extra label Pattern | Decision | Constraint
- Feature {uid, name, request, status}; Commit {sha, message, type, date}; Evidence {path, line_start}
Relationships:
(File)-[:DEFINES]->(Symbol), (Symbol)-[:HAS_MEMBER]->(Symbol method), (Symbol)-[:USES]->(Symbol),
(Symbol)-[:CALLS]->(Symbol), (Symbol)-[:ACCESSES {op}]->(Symbol:Model), (Symbol:Route)-[:HANDLES]->(Symbol),
(Symbol)-[:EXTENDS|IMPLEMENTS]->(Symbol), (File:TestFile)-[:COVERS]->(Symbol), (File)-[:IMPORTS]->(File),
(Module)-[:DEPENDS_ON]->(Module), (Symbol)-[:FOLLOWS]->(Pattern), (Symbol)-[:CONSTRAINED_BY]->(Constraint),
(Decision)-[:ESTABLISHES]->(Knowledge), (Knowledge)-[:SUPPORTED_BY]->(Evidence), (Commit)-[:TOUCHES]->(File),
(File)-[:CO_CHANGES {count}]->(File), (Feature)-[:MODIFIES|REUSES|INTRODUCES]->(Symbol)
Methods belong to classes via HAS_MEMBER: to find what a class depends on, include its members:
(c)-[:HAS_MEMBER*0..1]->()-[:USES|CALLS]->(t)."""

EXAMPLES = [
    "USER INPUT: 'How many controllers depend on TeamService?' QUERY: "
    "MATCH (c:Symbol:Controller {project_id: $project_id})-[:HAS_MEMBER*0..1]->()-[:USES|CALLS]->(t:Symbol) "
    "OPTIONAL MATCH (o:Symbol)-[:HAS_MEMBER]->(t) WITH c, coalesce(o, t) AS target "
    "WHERE target.name = 'TeamService' RETURN count(DISTINCT c) AS controllers",
    "USER INPUT: 'Which tables does TokenRepository access?' QUERY: "
    "MATCH (r:Symbol {project_id: $project_id, name: 'TokenRepository'})-[:HAS_MEMBER*0..1]->()-[a:ACCESSES]->"
    "(m:Symbol:Model) RETURN DISTINCT m.table AS table, collect(DISTINCT a.op) AS ops",
    "USER INPUT: 'List validated constraints' QUERY: "
    "MATCH (k:Constraint {project_id: $project_id, status: 'validated'}) "
    "RETURN k.title AS title, k.confidence AS confidence",
    "USER INPUT: 'Which routes have no validation?' QUERY: "
    "MATCH (r:Symbol:Route {project_id: $project_id}) WHERE r.validated = false "
    "RETURN r.http_method + ' ' + r.http_path AS route",
    "USER INPUT: 'Which files change together with team.service.ts?' QUERY: "
    "MATCH (f:File {project_id: $project_id})-[c:CO_CHANGES]-(g:File) WHERE f.path ENDS WITH 'team.service.ts' "
    "RETURN g.path AS path, c.count AS times ORDER BY times DESC",
]


class UnsafeQuery(ValueError):
    """The generated Cypher is not provably read-only; it was not executed."""


@dataclass
class AskResult:
    question: str
    cypher: str
    rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"question": self.question, "cypher": self.cypher, "rows": self.rows, "truncated": self.truncated}


def clean_cypher(text: str) -> str:
    text = _FENCE.sub("", text.strip()).strip()
    return text.rstrip(";").strip()


def static_check(cypher: str) -> None:
    m = _WRITE.search(cypher)
    if m:
        raise UnsafeQuery(f"refused: the query contains '{m.group(0).strip()}' (read-only questions only)")
    if ";" in cypher:
        raise UnsafeQuery("refused: multiple statements")


def ensure_limit(cypher: str, limit: int = MAX_ROWS) -> str:
    return cypher if re.search(r"\bLIMIT\s+\d+\s*$", cypher, re.IGNORECASE) else f"{cypher}\nLIMIT {limit}"


def run_guarded(store: Neo4jStore, cypher: str, project_id: str, timeout: float = 10.0) -> list[dict[str, Any]]:
    static_check(cypher)
    cypher = ensure_limit(cypher)
    query_type = store.explain_query_type(cypher, project_id=project_id)
    if query_type != "r":
        raise UnsafeQuery(f"refused: the server classifies this query as '{query_type}', not read-only")
    return store.read(cypher, timeout=timeout, project_id=project_id)


Generator = Callable[[str], str]


def graphrag_generator(model: str, api_key: str | None, base_url: str | None) -> Generator:
    """Cypher generation with neo4j-graphrag's Text2Cypher prompt and LLM interface (generation only)."""
    from neo4j_graphrag.generation.prompts import Text2CypherTemplate
    from neo4j_graphrag.llm import OpenAILLM

    llm = OpenAILLM(model_name=model, base_url=base_url, api_key=api_key)
    template = Text2CypherTemplate()

    def generate(question: str) -> str:
        prompt = template.format(schema=SCHEMA, examples="\n".join(EXAMPLES), query_text=question)
        return str(llm.invoke(prompt).content)

    return generate


def ask(store: Neo4jStore, project_id: str, question: str, generate: Generator) -> AskResult:
    cypher = clean_cypher(generate(question))
    rows = run_guarded(store, cypher, project_id)
    return AskResult(question, ensure_limit(cypher), rows, truncated=len(rows) >= MAX_ROWS)
