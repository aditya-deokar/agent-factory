"""Reuse detection (spec §16): before a new abstraction is created, find what already exists.

score = 0.35·vector + 0.25·name + 0.25·methods + 0.10·maturity + 0.05·pattern
verdict: >= 0.70 reuse · 0.50–0.70 extend · < 0.50 new_ok

The detector supplies evidence; the agent and the developer decide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ..auditor.cards import split_words
from ..auditor.patterns import LIFECYCLES
from ..common.embed import Embedder, EmbeddingUnavailable, HashEmbedder, cosine
from ..db.stores import Neo4jStore
from .query import singular
from .retrievers import fulltext_symbols, lucene_query

WEIGHTS = {"vector": 0.30, "name": 0.25, "methods": 0.25, "maturity": 0.10, "pattern": 0.05, "role": 0.05}
REUSE_AT, EXTEND_AT = 0.70, 0.50
ROLE_WORDS = {
    "service",
    "repository",
    "repo",
    "controller",
    "manager",
    "helper",
    "util",
    "utils",
    "handler",
    "provider",
    "client",
    "store",
    "impl",
    "base",
    "factory",
}
VERBS: dict[str, set[str]] = {
    "create": {"create", "issue", "generate", "mint", "make", "add", "insert", "new", "register"},
    "validate": {"validate", "verify", "confirm", "check", "find", "lookup", "get", "read", "fetch", "load"},
    "consume": {"consume", "redeem", "use", "burn", "accept", "claim"},
    "expire": {"expire", "revoke", "invalidate", "purge", "cleanup", "delete", "remove", "cancel"},
    "send": {"send", "dispatch", "deliver", "notify", "email", "mail"},
    "update": {"update", "set", "change", "edit", "patch", "save"},
    "list": {"list", "all", "search", "query"},
}


class ProposedAbstraction(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)
    methods: list[str] = Field(default_factory=list)
    role: str | None = None


class ReuseCandidate(BaseModel):
    uid: str
    name: str
    kind: str
    role: str | None = None
    path: str
    line: int | None = None
    score: float
    verdict: str
    features: dict[str, float] = Field(default_factory=dict)
    methods: list[str] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)
    lifecycle: str | None = None
    patterns: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)  # constraints / decisions that apply


class ReuseReport(BaseModel):
    proposed: ProposedAbstraction
    verdict: str
    recommendation: str = ""
    candidates: list[ReuseCandidate] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def top(self) -> ReuseCandidate | None:
        return self.candidates[0] if self.candidates else None


def normalize_verb(method: str) -> str | None:
    words = split_words(method)
    if not words:
        return None
    head = words[0]
    for verb, group in VERBS.items():
        if head in group or head.startswith(tuple(group)):
            return verb
    return head


# True synonyms only (not associations): used to compare names, so "MailerService" matches "EmailService".
NAME_SYNONYMS = {
    "mailer": "email",
    "mail": "email",
    "emailer": "email",
    "invite": "invitation",
    "repo": "repository",
    "auth": "authentication",
    "authn": "authentication",
    "org": "organization",
    "account": "user",
    "notifier": "notification",
    "msg": "message",
    "cfg": "config",
    "configuration": "config",
    "otp": "token",
    "nonce": "token",
    "code": "token",
    "pwd": "password",
    "passwd": "password",
    "smtp": "email",
    "confirm": "verification",
    "confirmation": "verification",
    "verify": "verification",
}
ROLE_SUFFIXES = {
    "service": "Service",
    "repository": "Repository",
    "repo": "Repository",
    "store": "Repository",
    "controller": "Controller",
    "client": "Integration",
    "gateway": "Integration",
    "adapter": "Integration",
    "middleware": "Middleware",
    "worker": "Worker",
    "validator": "Validator",
}


def core_tokens(name: str) -> set[str]:
    tokens = {NAME_SYNONYMS.get(singular(w), singular(w)) for w in split_words(name)}
    core = tokens - ROLE_WORDS
    return core or tokens


def name_similarity(proposed: str, existing: str, description: str = "") -> float:
    a, b = core_tokens(proposed), core_tokens(existing)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    containment = len(a & b) / len(b)  # how much of the existing name the proposal repeats
    described = {NAME_SYNONYMS.get(singular(w), singular(w)) for w in split_words(description)} | a
    in_description = len(b & described) / len(b)  # the existing name is what the proposal says it does
    return round(max(jaccard, 0.9 * containment, 0.8 * in_description), 3)


def inferred_role(name: str) -> str | None:
    words = split_words(name)
    return ROLE_SUFFIXES.get(singular(words[-1])) if words else None


def method_overlap(proposed: list[str], existing: list[str]) -> float:
    a = {v for v in (normalize_verb(m) for m in proposed) if v}
    b = {v for v in (normalize_verb(m) for m in existing) if v}
    if not a or not b:
        return 0.0
    # "Does the existing symbol already do what is proposed?" (coverage), with Jaccard as the floor.
    return round(max(len(a & b) / len(a), len(a & b) / len(a | b)), 3)


def maturity(fan_in: int) -> float:
    return round(min(1.0, math.log2(1 + fan_in) / math.log2(9)), 3)


def verdict_for(score: float) -> str:
    return "reuse" if score >= REUSE_AT else "extend" if score >= EXTEND_AT else "new_ok"


@dataclass
class _Row:
    data: dict[str, Any]
    sources: set[str] = field(default_factory=set)


CANDIDATE_INFO = """
UNWIND $uids AS uid
MATCH (s:Symbol {uid: uid}) WHERE s.parent IS NULL AND s.kind IN ['class', 'function']
OPTIONAL MATCH (u:Symbol)-[:USES|CALLS]->(x:Symbol) WHERE (x = s OR (s)-[:HAS_MEMBER]->(x))
OPTIONAL MATCH (uo:Symbol)-[:HAS_MEMBER]->(u)
WITH s, [v IN collect(DISTINCT coalesce(uo, u)) WHERE v.uid <> s.uid AND v.kind IN ['class', 'function']
         | v.name] AS users
OPTIONAL MATCH (s)-[:FOLLOWS]->(p:Pattern) WHERE p.status IN ['validated', 'candidate']
OPTIONAL MATCH (s)-[:CONSTRAINED_BY]->(c:Knowledge {status: 'validated'})
OPTIONAL MATCH (d:Decision {status: 'validated'})-[:ESTABLISHES]->(p)
RETURN s.uid AS uid, s.name AS name, s.kind AS kind, coalesce(s.roles, []) AS roles, s.path AS path,
       s.line_start AS line, coalesce(s.methods, []) AS methods, s.card AS card, s.embedding AS embedding,
       users, collect(DISTINCT {title: p.title, status: p.status, detector: p.detector, claim: p.claim}) AS patterns,
       collect(DISTINCT c.title) + collect(DISTINCT d.title) AS knowledge
"""


class ReuseDetector:
    def __init__(self, store: Neo4jStore, project_id: str, embedder: Embedder | None = None):
        self.store = store
        self.project_id = project_id
        self.embedder = embedder

    def _candidates(self, p: ProposedAbstraction, query_vec: list[float] | None, warnings: list[str]) -> list[str]:
        uids: list[str] = []
        core = sorted(core_tokens(p.name))
        words = core + [w for w in split_words(p.description) if len(w) > 3][:8]
        uids += [h.uid for h in fulltext_symbols(self.store, self.project_id, words, k=25)]
        if query_vec is not None:
            rows = self.store.read(
                "CALL db.index.vector.queryNodes('af_symbol_embedding', 60, $v) YIELD node, score "
                "WHERE node.project_id = $p RETURN node.uid AS uid",
                v=query_vec,
                p=self.project_id,
            )
            uids += [r["uid"] for r in rows]
        verbs = {normalize_verb(m) for m in p.methods} - {None}
        if verbs:
            rows = self.store.read(
                "MATCH (s:Symbol {project_id: $p}) WHERE s.kind = 'class' AND size(coalesce(s.methods, [])) > 0 "
                "RETURN s.uid AS uid, s.methods AS methods",
                p=self.project_id,
            )
            uids += [r["uid"] for r in rows if method_overlap(p.methods, r["methods"]) >= 0.5]
        return list(dict.fromkeys(uids))

    def find(self, p: ProposedAbstraction, limit: int = 5) -> ReuseReport:
        warnings: list[str] = []
        text = " ".join([p.name, *split_words(p.name), p.description, *p.methods])
        query_vec: list[float] | None = None
        if self.embedder is not None:
            try:
                query_vec = list(self.embedder.embed([text])[0])
            except EmbeddingUnavailable as error:
                warnings.append(f"semantic similarity unavailable ({error}); using a lexical fallback")
        uids = self._candidates(p, query_vec, warnings)
        rows = self.store.read(CANDIDATE_INFO, uids=uids) if uids else []
        lexical = HashEmbedder(256)
        lex_query = lexical.embed([text])[0]
        candidates: list[ReuseCandidate] = []
        for row in rows:
            if row["name"] == p.name or {"Route", "Model"} & set(row["roles"]):
                continue
            if query_vec is not None and row.get("embedding"):
                vec = max(0.0, cosine(query_vec, row["embedding"]))
            else:
                vec = max(0.0, cosine(lex_query, lexical.embed([row["card"] or row["name"]])[0]))
            patterns = [x for x in row["patterns"] if x.get("title")]
            validated_pattern = any(x["status"] == "validated" for x in patterns)
            features = {
                "vector": round(vec, 3),
                "name": name_similarity(p.name, row["name"], p.description),
                "methods": method_overlap(p.methods or split_words(p.description), row["methods"]),
                "maturity": maturity(len(row["users"])),
                "pattern": 1.0 if validated_pattern else 0.0,
                "role": 1.0 if (p.role or inferred_role(p.name)) in row["roles"] else 0.0,
            }
            score = round(sum(WEIGHTS[k] * v for k, v in features.items()), 3)
            lifecycle = next((x["claim"] for x in patterns if x.get("detector") == "lifecycle_verbs"), None)
            if lifecycle is None and len({normalize_verb(m) for m in row["methods"]} & set(LIFECYCLES["token"])) >= 3:
                lifecycle = " → ".join(m for m in row["methods"] if normalize_verb(m) in LIFECYCLES["token"])
            candidates.append(
                ReuseCandidate(
                    uid=row["uid"],
                    name=row["name"],
                    kind=row["kind"],
                    role=(row["roles"] or [None])[0],
                    path=row["path"],
                    line=row["line"],
                    score=score,
                    verdict=verdict_for(score),
                    features=features,
                    methods=list(row["methods"]),
                    used_by=sorted(row["users"]),
                    lifecycle=lifecycle,
                    patterns=[x["title"] for x in patterns],
                    knowledge=sorted({k for k in row["knowledge"] if k}),
                )
            )
        candidates.sort(key=lambda c: (-c.score, c.name))
        candidates = candidates[:limit]
        verdict = candidates[0].verdict if candidates else "new_ok"
        return ReuseReport(
            proposed=p,
            verdict=verdict,
            recommendation=_recommend(p, candidates),
            candidates=candidates,
            warnings=warnings,
        )


def _recommend(p: ProposedAbstraction, cands: list[ReuseCandidate]) -> str:
    if not cands or cands[0].verdict == "new_ok":
        return (
            f"No existing implementation covers {p.name}. A new abstraction looks justified; "
            "say why in the plan and follow the validated patterns for its role."
        )
    top = cands[0]
    if top.verdict == "reuse":
        return (
            f"Evaluate whether {top.name} can support this (e.g. a new purpose or parameter) "
            f"before introducing {p.name}."
        )
    return f"Extend {top.name} (a parameter, a method or a strategy) rather than adding a sibling of it."


def render_reuse(report: ReuseReport) -> str:
    p = report.proposed
    lines = [f"Proposed: {p.name}"]
    if not report.candidates:
        lines.append("No similar implementation found.")
    for i, c in enumerate(report.candidates):
        head = "Potential existing implementation" if i == 0 else "Other candidate"
        lines.append(f"{head}: {c.name}  (score {c.score} · verdict: {c.verdict})")
        if i == 0:
            lines.append(f"  {c.path}:{c.line}" if c.line else f"  {c.path}")
            if c.lifecycle:
                lines.append(f"  Existing lifecycle: {c.lifecycle}")
            elif c.methods:
                lines.append(f"  Methods: {', '.join(c.methods[:10])}")
            if c.used_by:
                lines.append(f"  Already reused by: {', '.join(c.used_by[:8])}")
            for k in c.knowledge[:3]:
                lines.append(f"  Applies: {k}")
            lines.append("  Why: " + ", ".join(f"{k} {v}" for k, v in c.features.items()))
    lines.append(f"Recommendation: {report.recommendation}")
    lines += [f"Note: {w}" for w in report.warnings]
    return "\n".join(lines)


__all__ = ["ProposedAbstraction", "ReuseCandidate", "ReuseDetector", "ReuseReport", "lucene_query", "render_reuse"]
