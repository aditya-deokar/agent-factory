"""Retrievers: each turns a query into ranked symbol hits (owner level: classes, functions, routes, models).

vector   neo4j-graphrag VectorCypherRetriever over symbol cards (af_symbol_embedding)
fulltext Lucene over names, camelCase tokens, method names and docs (af_symbol_text)
code     ripgrep over the repository, mapped to the enclosing symbol
docs     VectorCypherRetriever over doc chunks, following DESCRIBES to symbols
history  commits whose message matches, via TOUCHES to the symbols of those files
"""

from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from neo4j_graphrag.embeddings.base import Embedder as GraphRagEmbedderBase
from neo4j_graphrag.retrievers import VectorCypherRetriever
from neo4j_graphrag.types import RetrieverResultItem

from ..common.embed import Embedder
from ..db.stores import Neo4jStore

OVERSAMPLE = 3
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)')


@dataclass
class Hit:
    uid: str
    score: float
    source: str
    why: str


@dataclass
class DocHit:
    uid: str
    path: str
    heading: str
    excerpt: str
    score: float
    describes: list[str] = field(default_factory=list)


class GraphRagEmbedder(GraphRagEmbedderBase):
    """Adapts Agent Factory's Embedder to neo4j-graphrag's interface."""

    def __init__(self, inner: Embedder):
        super().__init__()
        self.inner = inner

    def embed_query(self, text: str) -> list[float]:
        return list(self.inner.embed([text])[0])


def _metadata(record: Any) -> RetrieverResultItem:
    data = record.data()
    return RetrieverResultItem(
        content=json.dumps({k: v for k, v in data.items() if k != "embedding"}, default=str), metadata=data
    )


VECTOR_SYMBOL_QUERY = """
WITH node, score WHERE node.project_id = $project_id
OPTIONAL MATCH (owner:Symbol)-[:HAS_MEMBER]->(node)
WITH coalesce(owner, node) AS s, score
OPTIONAL MATCH (s)-[:FOLLOWS]->(p:Pattern {status: 'validated'})
OPTIONAL MATCH (s)<-[:USES|CALLS]-(user:Symbol)
RETURN s.uid AS uid, s.name AS name, s.path AS path, s.card AS card, score,
       collect(DISTINCT p.title) AS patterns, count(DISTINCT user) AS fan_in
"""

VECTOR_CHUNK_QUERY = """
WITH node, score WHERE node.project_id = $project_id
MATCH (node)-[:FROM_DOC]->(d:Doc)
OPTIONAL MATCH (node)-[:DESCRIBES]->(s:Symbol)
RETURN node.uid AS uid, d.path AS path, node.heading AS heading, left(node.text, 400) AS excerpt, score,
       collect(DISTINCT s.uid) AS describes
"""


def vector_symbols(store: Neo4jStore, project_id: str, embedder: Embedder, text: str, k: int = 20) -> list[Hit]:
    retriever = VectorCypherRetriever(
        store.driver,
        index_name="af_symbol_embedding",
        retrieval_query=VECTOR_SYMBOL_QUERY,
        embedder=GraphRagEmbedder(embedder),
        result_formatter=_metadata,
        neo4j_database=store.database,
    )
    result = retriever.search(query_text=text, top_k=k * OVERSAMPLE, query_params={"project_id": project_id})
    best: dict[str, Hit] = {}
    for item in result.items:
        m = item.metadata or {}
        uid = m.get("uid")
        if not uid:
            continue
        score = float(m.get("score") or 0.0)
        if uid not in best or best[uid].score < score:
            best[uid] = Hit(uid, score, "vector", f"vector {score:.2f}")
    return sorted(best.values(), key=lambda h: -h.score)[:k]


def vector_docs(store: Neo4jStore, project_id: str, embedder: Embedder, text: str, k: int = 6) -> list[DocHit]:
    retriever = VectorCypherRetriever(
        store.driver,
        index_name="af_chunk_embedding",
        retrieval_query=VECTOR_CHUNK_QUERY,
        embedder=GraphRagEmbedder(embedder),
        result_formatter=_metadata,
        neo4j_database=store.database,
    )
    result = retriever.search(query_text=text, top_k=k * OVERSAMPLE, query_params={"project_id": project_id})
    hits = []
    for item in result.items:
        m = item.metadata or {}
        if m.get("uid"):
            hits.append(
                DocHit(
                    m["uid"],
                    m["path"],
                    m.get("heading") or "",
                    m.get("excerpt") or "",
                    float(m.get("score") or 0.0),
                    list(m.get("describes") or []),
                )
            )
    return hits[:k]


def lucene_query(terms: list[str]) -> str:
    parts = []
    for t in terms:
        esc = _LUCENE_SPECIAL.sub(r"\\\1", t)
        parts.append(f"{esc}*" if len(t) >= 4 else esc)
    return " OR ".join(parts)


def fulltext_symbols(store: Neo4jStore, project_id: str, terms: list[str], k: int = 30) -> list[Hit]:
    if not terms:
        return []
    rows = store.read(
        """
        CALL db.index.fulltext.queryNodes('af_symbol_text', $q) YIELD node, score
        WHERE node.project_id = $p
        OPTIONAL MATCH (owner:Symbol)-[:HAS_MEMBER]->(node)
        WITH coalesce(owner, node) AS s, max(score) AS score
        RETURN s.uid AS uid, score ORDER BY score DESC LIMIT $k
        """,
        q=lucene_query(terms),
        p=project_id,
        k=k,
    )
    return [Hit(r["uid"], float(r["score"]), "fulltext", f"full-text {r['score']:.2f}") for r in rows]


def _symbol_spans(store: Neo4jStore, project_id: str, paths: list[str]) -> dict[str, list[dict[str, Any]]]:
    rows = store.read(
        """
        MATCH (f:File {project_id: $p})-[:DEFINES]->(s:Symbol) WHERE f.path IN $paths AND NOT f:TestFile
        RETURN f.path AS path, s.uid AS uid, s.line_start AS a, s.line_end AS b, s.kind AS kind, s.parent AS parent
        """,
        p=project_id,
        paths=paths,
    )
    spans: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        spans[r["path"]].append(r)
    return spans


def _enclosing(spans: list[dict[str, Any]], line: int) -> str | None:
    inside = [s for s in spans if (s["a"] or 0) <= line <= (s["b"] or 0) and s["kind"] != "method"]
    if not inside:
        return None
    return min(inside, key=lambda s: (s["b"] or 0) - (s["a"] or 0))["uid"]


def code_search(root: Path, store: Neo4jStore, project_id: str, terms: list[str], k: int = 20) -> list[Hit]:
    """Exact (case-insensitive, word) matches in the working tree, credited to the enclosing symbol."""
    terms = [t for t in terms if len(t) >= 3]
    if not terms:
        return []
    matches: dict[tuple[str, int], set[str]] = defaultdict(set)
    rg = shutil.which("rg")
    pattern = re.compile(r"(?i)(" + "|".join(map(re.escape, terms)) + r")")
    code_paths = [
        r["path"]
        for r in store.read("MATCH (f:File {project_id: $p}) WHERE NOT f:TestFile RETURN f.path AS path", p=project_id)
    ]
    if rg:
        args = [rg, "--json", "-i", "--max-count", "200"]
        for t in terms:
            args += ["-e", re.escape(t)]
        for ext in ("ts", "tsx", "js", "jsx", "mjs", "cjs", "py"):
            args += ["-g", f"*.{ext}"]
        out = subprocess.run(
            [*args, "."], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
        ).stdout
        wanted = set(code_paths)
        for raw in out.splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event["data"]
            path = data["path"]["text"].replace("\\", "/").removeprefix("./")
            if path not in wanted:
                continue
            found = {m.group(1).lower() for m in pattern.finditer(data["lines"]["text"])}
            matches[(path, int(data["line_number"]))] |= found
    else:
        for path in code_paths:
            try:
                text = (root / path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), start=1):
                found = {m.group(1).lower() for m in pattern.finditer(line)}
                if found:
                    matches[(path, i)] |= found
    spans = _symbol_spans(store, project_id, sorted({p for p, _ in matches}))
    per_symbol: dict[str, tuple[set[str], int]] = {}
    for (path, line_no), found in matches.items():
        uid = _enclosing(spans.get(path, []), line_no)
        if uid is None:
            continue
        seen, count = per_symbol.get(uid, (set(), 0))
        per_symbol[uid] = (seen | found, count + 1)
    hits = [
        Hit(uid, len(seen) + math.log1p(count) / 10, "code", f"code: {', '.join(sorted(seen))} ×{count}")
        for uid, (seen, count) in per_symbol.items()
    ]
    return sorted(hits, key=lambda h: (-h.score, h.uid))[:k]


def history_symbols(store: Neo4jStore, project_id: str, terms: list[str], k: int = 15) -> list[Hit]:
    terms = [t for t in terms if len(t) >= 4]
    if not terms:
        return []
    rows = store.read(
        """
        MATCH (c:Commit {project_id: $p})
        WHERE any(t IN $terms WHERE toLower(c.message) CONTAINS t)
        MATCH (c)-[:TOUCHES]->(f:File)-[:DEFINES]->(s:Symbol)
        WHERE s.parent IS NULL AND NOT f:TestFile AND s.kind IN ['class', 'function']
        RETURN s.uid AS uid, count(DISTINCT c) AS commits, collect(DISTINCT c.message)[..2] AS msgs
        ORDER BY commits DESC, uid LIMIT $k
        """,
        p=project_id,
        terms=terms,
        k=k,
    )
    return [Hit(r["uid"], float(r["commits"]), "history", f"history: {r['commits']} commits") for r in rows]


@dataclass
class Fused:
    uid: str
    score: float
    why: list[str]
    sources: set[str]


def rrf(rankings: dict[str, list[Hit]], k: int = 60, weights: dict[str, float] | None = None) -> list[Fused]:
    """Reciprocal Rank Fusion: robust to incomparable score scales; keeps the reasons for every hit."""
    fused: dict[str, Fused] = {}
    for source, hits in rankings.items():
        w = (weights or {}).get(source, 1.0)
        for rank, hit in enumerate(hits, start=1):
            entry = fused.setdefault(hit.uid, Fused(hit.uid, 0.0, [], set()))
            entry.score += w / (k + rank)
            entry.why.append(f"{hit.why} (#{rank})")
            entry.sources.add(source)
    return sorted(fused.values(), key=lambda f: (-f.score, f.uid))
