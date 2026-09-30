"""The context engine (spec §15): request -> task-specific context pack.

  query understanding -> retrievers (vector, full-text, code, docs, history) -> RRF seeds
  -> graph expansion (USES/CALLS/HANDLES/ACCESSES/CO_CHANGES, decayed) -> knowledge attach
  -> agent memory -> budgeted pack

Graph expansion is where graph thinking pays off: EmailService reaches "Add team invitations"
through the services that already combine tokens and email, even when the request never says "email".
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.embed import Embedder, EmbeddingUnavailable
from ..db.stores import Neo4jStore
from ..memory.agent_memory import AgentMemoryPort, project_session
from .pack import (
    ArchItem,
    ContextPack,
    DepItem,
    DocItem,
    FeatureItem,
    KnowledgeItem,
    MemoryItem,
    SymbolItem,
    TestItem,
    TraceItem,
    apply_budget,
)
from .query import ParsedQuery, understand
from .retrievers import (
    DocHit,
    Fused,
    Hit,
    code_search,
    fulltext_symbols,
    history_symbols,
    rrf,
    vector_docs,
    vector_symbols,
)
from .token_economy import compute_token_economy

ALL_MODES = frozenset({"vector", "fulltext", "code", "docs", "history", "graph"})
SEEDS = 12
RELEVANT = 16
EDGE_WEIGHT = {"USES": 1.0, "CALLS": 0.8, "HANDLES": 0.8, "ACCESSES": 0.7, "CO_CHANGES": 0.5}
DECAY = 0.5
GRAPH_WEIGHT = 0.25  # how much structure can reorder retrieval (tuned on the eval 'tune' split)
DEGREE_NORM = True  # divide by sqrt(degree) so hubs do not dominate
SOURCE_WEIGHT = {"vector": 1.0, "fulltext": 1.0, "code": 0.8, "docs": 0.7, "history": 0.5}
LAYER = ["Route", "Controller", "Service", "Integration", "Repository", "Model"]
NOT_REUSABLE = {"Route", "Controller", "Model"}
ACTIVE = ("validated",)

NEIGHBOURS = """
UNWIND $uids AS uid
MATCH (s:Symbol {uid: uid})
OPTIONAL MATCH (s)-[:HAS_MEMBER]->(m:Symbol)
WITH s, [s] + collect(m) AS parts
UNWIND parts AS part
MATCH (part)-[r:USES|CALLS|ACCESSES|HANDLES]-(n:Symbol)
OPTIONAL MATCH (o:Symbol)-[:HAS_MEMBER]->(n)
WITH s, type(r) AS rel, coalesce(o, n) AS nb
WHERE nb.uid <> s.uid AND nb.project_id = s.project_id AND nb.kind <> 'const'
RETURN DISTINCT s.uid AS src, rel, nb.uid AS dst
UNION
UNWIND $uids AS uid
MATCH (s:Symbol {uid: uid})<-[:DEFINES]-(:File)-[:CO_CHANGES]-(:File)-[:DEFINES]->(t:Symbol)
WHERE t.parent IS NULL AND t.kind IN ['class', 'function']
RETURN DISTINCT s.uid AS src, 'CO_CHANGES' AS rel, t.uid AS dst
"""

INFO = """
UNWIND $uids AS uid
MATCH (s:Symbol {uid: uid})
OPTIONAL MATCH (s)-[:HAS_MEMBER*0..1]->()-[:USES|CALLS|ACCESSES]->(t:Symbol)
OPTIONAL MATCH (to:Symbol)-[:HAS_MEMBER]->(t)
WITH s, [d IN collect(DISTINCT coalesce(to, t)) WHERE d.uid <> s.uid AND d.kind <> 'const'
         | {uid: d.uid, name: d.name, roles: d.roles, table: d.table}] AS deps
OPTIONAL MATCH (u:Symbol)-[:USES|CALLS|HANDLES]->(x:Symbol) WHERE x = s OR (s)-[:HAS_MEMBER]->(x)
OPTIONAL MATCH (uo:Symbol)-[:HAS_MEMBER]->(u)
WITH s, deps, [v IN collect(DISTINCT coalesce(uo, u)) WHERE v.uid <> s.uid AND v.kind <> 'const'
               | {uid: v.uid, name: v.name, roles: v.roles, kind: v.kind}] AS users
OPTIONAL MATCH (s)-[:FOLLOWS]->(p:Pattern {status: 'validated'})
RETURN s.uid AS uid, s.name AS name, s.kind AS kind, coalesce(s.roles, []) AS roles, s.path AS path,
       s.line_start AS line, coalesce(s.methods, []) AS methods, s.doc AS doc, s.table AS table,
       deps, users, collect(DISTINCT p.title) AS patterns
"""

KNOWLEDGE = """
UNWIND $uids AS uid
MATCH (:Symbol {uid: uid})-[:FOLLOWS|CONSTRAINED_BY]->(k:Knowledge)
RETURN DISTINCT k.uid AS uid
UNION
MATCH (k:Constraint {project_id: $p, status: 'validated'}) WHERE k.rule_type = 'forbid_external_dep'
RETURN k.uid AS uid
UNION
UNWIND $uids AS uid
MATCH (:Symbol {uid: uid})-[:FOLLOWS|CONSTRAINED_BY]->(:Knowledge)<-[:ESTABLISHES]-(d:Decision)
RETURN DISTINCT d.uid AS uid
"""

KNOWLEDGE_DETAIL = """
UNWIND $uids AS uid
MATCH (k:Knowledge {uid: uid})
OPTIONAL MATCH (k)-[:SUPPORTED_BY]->(e:Evidence)
WITH k, e ORDER BY e.path, e.line_start
WITH k, collect(e.path + CASE WHEN e.line_start IS NULL THEN '' ELSE ':' + toString(e.line_start) END)[..3] AS evidence
RETURN k.uid AS uid, k.kind AS kind, k.title AS title, k.claim AS claim, k.status AS status,
       k.confidence AS confidence, k.support_count AS support, k.violation_count AS violations,
       k.rule_type AS rule_type, evidence
"""


@dataclass
class Retrieval:
    query: ParsedQuery
    rankings: dict[str, list[Hit]] = field(default_factory=dict)
    fused: list[Fused] = field(default_factory=list)
    docs: list[DocHit] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)  # after expansion
    why: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def ranked(self, n: int | None = None) -> list[str]:
        """Owner-level symbols by relevance. Routes are reached through their handlers, not listed."""
        order = sorted(
            (u for u in self.scores if not u.split("#", 1)[-1].startswith("route:")), key=lambda u: (-self.scores[u], u)
        )
        return order[:n] if n else order


class ContextEngine:
    def __init__(
        self,
        store: Neo4jStore,
        project_id: str,
        root: Path,
        embedder: Embedder | None = None,
        memory: AgentMemoryPort | None = None,
    ):
        self.store = store
        self.project_id = project_id
        self.root = root
        self.embedder = embedder
        self.memory = memory

    # -- retrieval ---------------------------------------------------------------------------------------------------

    def retrieve(self, request: str, modes: frozenset[str] = ALL_MODES) -> Retrieval:
        pq = understand(request, self.root)
        r = Retrieval(pq)
        terms = pq.all_terms + [i.lower() for i in pq.identifiers]
        vector_text = " ".join([request, *pq.expanded])
        if self.embedder is not None and ("vector" in modes or "docs" in modes):
            try:
                if "vector" in modes:
                    r.rankings["vector"] = vector_symbols(self.store, self.project_id, self.embedder, vector_text)
                if "docs" in modes:
                    r.docs = vector_docs(self.store, self.project_id, self.embedder, vector_text)
                    doc_hits: dict[str, Hit] = {}
                    for d in r.docs:
                        for uid in d.describes:
                            if uid not in doc_hits:
                                doc_hits[uid] = Hit(uid, d.score, "docs", f"docs: {d.path}")
                    r.rankings["docs"] = sorted(doc_hits.values(), key=lambda h: -h.score)
            except EmbeddingUnavailable as error:
                r.warnings.append(f"semantic search unavailable ({error}); using keyword + graph retrieval")
        elif "vector" in modes:
            r.warnings.append("no embeddings configured; using keyword + graph retrieval")
        if "fulltext" in modes:
            r.rankings["fulltext"] = fulltext_symbols(self.store, self.project_id, terms)
        if "code" in modes:
            r.rankings["code"] = code_search(self.root, self.store, self.project_id, pq.terms + pq.identifiers)
        if "history" in modes:
            r.rankings["history"] = history_symbols(self.store, self.project_id, pq.terms)
        r.fused = [
            f
            for f in rrf({k: v for k, v in r.rankings.items() if v}, weights=SOURCE_WEIGHT)
            if ":route:" not in f"{f.uid.split('#', 1)[-1][:6]}" and not f.uid.split("#", 1)[-1].startswith("route:")
        ]
        top = r.fused[:SEEDS]
        best = top[0].score if top else 1.0
        for f in top:
            r.scores[f.uid] = f.score / best
            r.why[f.uid] = list(f.why)
        if "graph" in modes and top:
            self._expand(r)
        return r

    def _expand(self, r: Retrieval) -> None:
        """Spread relevance along architectural edges. Contributions ADD UP, so a symbol connected to several
        relevant symbols rises (one step of personalised PageRank, two hops, decayed)."""
        frontier = dict(r.scores)
        for hop in (1, 2):
            if not frontier:
                break
            rows = self.store.read(NEIGHBOURS, uids=list(frontier))
            degree: dict[str, int] = defaultdict(int)
            for row in rows:
                degree[row["src"]] += 1
            gained: dict[str, float] = defaultdict(float)
            for row in rows:
                norm = math.sqrt(degree[row["src"]]) if DEGREE_NORM else 1.0
                contribution = frontier.get(row["src"], 0.0) * DECAY * EDGE_WEIGHT.get(row["rel"], 0.5) / norm
                if contribution <= 0:
                    continue
                gained[row["dst"]] += contribution
                if len(r.why.get(row["dst"], [])) < 6:
                    r.why.setdefault(row["dst"], []).append(
                        f"graph: {row['rel']} {row['src'].split('#')[-1]} (hop {hop})"
                    )
            for uid, g in gained.items():
                r.scores[uid] = r.scores.get(uid, 0.0) + GRAPH_WEIGHT * g * (DECAY if hop == 2 else 1.0)
            frontier = dict(sorted(gained.items(), key=lambda kv: -kv[1])[:24])

    # -- pack --------------------------------------------------------------------------------------------------------

    def build(
        self, request: str, budget: int = 4000, include_history: bool = False, modes: frozenset[str] = ALL_MODES
    ) -> ContextPack:
        return asyncio.run(self.abuild(request, budget, include_history, modes))

    async def abuild(
        self, request: str, budget: int = 4000, include_history: bool = False, modes: frozenset[str] = ALL_MODES
    ) -> ContextPack:
        r = self.retrieve(request, modes)
        pack = ContextPack(
            request=request, project_id=self.project_id, terms=r.query.all_terms, warnings=list(r.warnings)
        )
        relevant = r.ranked(RELEVANT)
        info = {row["uid"]: row for row in self.store.read(INFO, uids=relevant)} if relevant else {}
        pack.reusable = [
            self._symbol_item(info[u], r)
            for u in relevant
            if u in info and not (set(info[u]["roles"]) & NOT_REUSABLE) and info[u]["kind"] in ("class", "function")
        ][:8]
        pack.architecture = self._flows(relevant, info)
        self._attach_knowledge(pack, relevant, include_history)
        pack.dependencies = [
            DepItem(uid=s.uid, name=s.name, dependents=s.used_by) for s in pack.reusable[:5] if s.used_by
        ]
        pack.tests = self._tests(relevant)
        for d, cov in zip(pack.dependencies, [self._tests([d.uid]) for d in pack.dependencies], strict=False):
            d.tests = [t.path for t in cov]
        pack.related_features = self._features(relevant)
        pack.docs = [DocItem(path=d.path, heading=d.heading, excerpt=d.excerpt) for d in r.docs[:3]]
        pack.memory = await self._memory(request)
        pack = apply_budget(pack, budget)
        targeted = {s.path for s in pack.reusable if s.path} | {t.path for t in pack.tests if t.path}
        try:
            rows = self.store.read("MATCH (f:File {project_id: $p}) RETURN count(f) AS cnt", p=self.project_id)
            total_files = rows[0]["cnt"] if rows else 0
        except Exception:
            total_files = 0
        tokens_used = pack.budget.used if pack.budget.used > 0 else 1000
        pack.token_economy = compute_token_economy(
            total_repo_files=total_files,
            targeted_files=len(targeted),
            graph_pack_tokens=tokens_used,
        )
        return pack

    def _symbol_item(self, row: dict[str, Any], r: Retrieval) -> SymbolItem:
        return SymbolItem(
            uid=row["uid"],
            name=row["name"],
            kind=row["kind"],
            role=(row["roles"] or [None])[0],
            path=row["path"],
            line=row["line"],
            score=round(r.scores.get(row["uid"], 0.0), 3),
            methods=list(row["methods"]),
            used_by=sorted({u["name"] for u in row["users"] if u.get("kind") != "route"}),
            patterns=list(row["patterns"]),
            doc=row["doc"],
            why=r.why.get(row["uid"], [])[:4],
        )

    def _flows(self, relevant: list[str], info: dict[str, dict[str, Any]]) -> list[ArchItem]:
        """Route → Controller → Service → Repository → Model chains through the relevant symbols."""
        flows: list[ArchItem] = []
        seen: set[str] = set()
        for uid in relevant:
            row = info.get(uid)
            if row is None or "Service" not in row["roles"] or uid in seen:
                continue
            seen.add(uid)
            ctrls = sorted({u["name"] for u in row["users"] if "Controller" in (u["roles"] or [])})
            deps = row["deps"]
            repos = [d for d in deps if "Repository" in (d["roles"] or [])]
            integ = sorted({d["name"] for d in deps if "Integration" in (d["roles"] or [])})
            services = sorted({d["name"] for d in deps if "Service" in (d["roles"] or [])})
            models = self._models([d["uid"] for d in repos])
            parts = []
            if ctrls:
                parts.append("/".join(ctrls))
            parts.append(row["name"])
            downstream = sorted({d["name"] for d in repos}) + services + integ
            if downstream:
                parts.append(", ".join(downstream))
            if models:
                parts.append(", ".join(models))
            flows.append(ArchItem(text=" → ".join(parts), uids=[uid]))
        return flows[:6]

    def _models(self, repo_uids: list[str]) -> list[str]:
        if not repo_uids:
            return []
        rows = self.store.read(
            "UNWIND $u AS uid MATCH (:Symbol {uid: uid})-[:HAS_MEMBER*0..1]->()-[:ACCESSES]->(m:Symbol) "
            "RETURN DISTINCT coalesce(m.table, m.name) AS t ORDER BY t",
            u=repo_uids,
        )
        return [row["t"] for row in rows]

    def _attach_knowledge(self, pack: ContextPack, relevant: list[str], include_history: bool) -> None:
        uids = [r["uid"] for r in self.store.read(KNOWLEDGE, uids=relevant, p=self.project_id)] if relevant else []
        uids += self._relevant_decisions(pack.terms)
        rows = self.store.read(KNOWLEDGE_DETAIL, uids=sorted(set(uids)))
        statuses = ("validated", "deprecated", "superseded") if include_history else ACTIVE
        for row in sorted(rows, key=lambda k: (-(k["confidence"] or 0), k["title"])):
            item = KnowledgeItem(**{**row, "evidence": [e for e in row["evidence"] if e]})
            if item.status == "candidate":
                pack.unverified.append(item)
            elif item.status in statuses:
                getattr(
                    pack, {"pattern": "patterns", "decision": "decisions", "constraint": "constraints"}[item.kind]
                ).append(item)

    def _relevant_decisions(self, terms: list[str]) -> list[str]:
        from .retrievers import lucene_query

        if not terms:
            return []
        rows = self.store.read(
            "CALL db.index.fulltext.queryNodes('af_knowledge_text', $q) YIELD node, score "
            "WHERE node.project_id = $p AND node:Decision RETURN node.uid AS uid, score ORDER BY score DESC LIMIT 3",
            q=lucene_query(terms[:8]),
            p=self.project_id,
        )
        return [r["uid"] for r in rows if r["score"] > 0.5]

    def _tests(self, uids: list[str]) -> list[TestItem]:
        if not uids:
            return []
        rows = self.store.read(
            "UNWIND $u AS uid MATCH (t:TestFile)-[:COVERS]->(s:Symbol {uid: uid}) "
            "RETURN t.path AS path, collect(DISTINCT s.name) AS covers ORDER BY path",
            u=uids,
        )
        return [TestItem(path=r["path"], covers=r["covers"]) for r in rows]

    def _features(self, uids: list[str]) -> list[FeatureItem]:
        if not uids:
            return []
        rows = self.store.read(
            """
            UNWIND $u AS uid
            MATCH (f:Feature)-[:MODIFIES|REUSES|INTRODUCES]->(s:Symbol {uid: uid})
            RETURN f.uid AS uid, f.name AS name, f.request AS request, f.status AS status, f.outcome AS outcome,
                   f.created_at AS created, collect(DISTINCT s.name) AS touched
            ORDER BY created DESC LIMIT 5
            """,
            u=uids,
        )
        return [FeatureItem(**{k: v for k, v in r.items() if k != "created"}) for r in rows]

    async def _memory(self, request: str) -> MemoryItem:
        if self.memory is None or not getattr(self.memory, "enabled", False):
            return MemoryItem()
        item = MemoryItem()
        try:
            item.preferences = await self.memory.recall_preferences(request, limit=6)
            item.similar_tasks = [
                TraceItem(task=t.task, outcome=t.outcome, success=t.success)
                for t in await self.memory.similar_traces(request, limit=3)
            ]
            notes = await self.memory.context(request, project_session(self.project_id))
            item.notes = notes.strip() or None
        except Exception as error:  # memory is additive: never fail the pack because of it
            item.notes = f"(agent memory unavailable: {type(error).__name__})"
        return item


def neighbours_by_rel(store: Neo4jStore, uids: list[str]) -> dict[str, list[tuple[str, str]]]:
    """Helper for tests and ablations: src -> [(rel, dst)]."""
    out: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for row in store.read(NEIGHBOURS, uids=uids):
        out[row["src"]].append((row["rel"], row["dst"]))
    return out
