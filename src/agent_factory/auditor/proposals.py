"""Turn detector output into Proposals for the KnowledgeService (evidence = symbol locations)."""

from __future__ import annotations

import re

from ..memory.validation import EvidenceRef, Proposal
from ..schema.model import KnowledgeKind, KnowledgeSource
from ..schema.uids import slug
from .constraints import ConstraintCandidate
from .docs import Adr, ProseRule
from .graphview import GraphView
from .patterns import PatternCandidate

MAX_REFS = 20
_STOP = {
    "the",
    "and",
    "for",
    "are",
    "not",
    "use",
    "with",
    "into",
    "only",
    "all",
    "must",
    "from",
    "that",
    "this",
    "through",
    "goes",
    "should",
    "never",
    "any",
    "their",
    "its",
    "new",
    "add",
    "adding",
    "instead",
    "live",
    "lives",
}


def refs_for(view: GraphView, uids: list[str], note: str | None = None) -> list[EvidenceRef]:
    out: list[EvidenceRef] = []
    files_by_uid = {f["uid"]: p for p, f in view.files.items()}
    for uid in uids[:MAX_REFS]:
        sym = view.symbols.get(uid)
        if sym is not None:
            out.append(
                EvidenceRef(path=sym.path, line_start=sym.line_start, line_end=sym.line_end, symbol_uid=uid, note=note)
            )
        elif uid in files_by_uid:
            out.append(EvidenceRef(path=files_by_uid[uid], note=note))
    return out


def pattern_proposal(view: GraphView, p: PatternCandidate) -> Proposal:
    return Proposal(
        kind=KnowledgeKind.PATTERN,
        key=p.key,
        title=p.title[:120],
        claim=p.claim[:1000],
        category=p.category,
        source=KnowledgeSource.AUDITOR,
        evidence=refs_for(view, p.support),
        contradictions=refs_for(view, p.violations, note=f"does not follow: {p.title}"[:300]),
        support_count=len(p.support),
        violation_count=len(p.violations),
        rederived=True,
        detector=p.detector,
    )


def constraint_proposal(view: GraphView, c: ConstraintCandidate, manifest: str | None) -> Proposal:
    evidence = refs_for(view, c.support)
    if not evidence and manifest:
        evidence = [EvidenceRef(path=manifest, note=f"declares {', '.join(c.rule.get('allowed', []))}")]
    return Proposal(
        kind=KnowledgeKind.CONSTRAINT,
        key=c.key,
        title=c.title[:120],
        claim=c.claim[:1000],
        category="architecture" if c.rule_type in ("forbid_dependency", "restrict_access") else c.rule_type,
        source=KnowledgeSource.AUDITOR,
        evidence=evidence,
        rule_type=c.rule_type,
        rule=c.rule,
        severity=c.severity,
        check_cypher=c.check_cypher,
        detector=f"constraint:{c.derived_from or c.rule_type}",
    )


def adr_proposal(adr: Adr) -> Proposal:
    claim = adr.decision or adr.title
    return Proposal(
        kind=KnowledgeKind.DECISION,
        key=adr.adr_id,
        title=f"{adr.adr_id}: {adr.title}"[:120],
        claim=claim[:1000],
        category="adr",
        source=KnowledgeSource.ADR,
        evidence=[EvidenceRef(path=adr.path, line_start=1, line_end=adr.line_end, kind="adr")],
        support_count=1,
        violation_count=0,
        adr_id=adr.adr_id,
        adr_status=adr.status,
        context=adr.context or None,
        consequences=adr.consequences or None,
        source_path=adr.path,
    )


def prose_proposal(rule: ProseRule) -> Proposal:
    return Proposal(
        kind=KnowledgeKind.CONSTRAINT,
        key=f"prose-{slug(rule.text, 50)}",
        title=rule.text[:120],
        claim=rule.text,
        category="prose",
        source=KnowledgeSource.AUDITOR,
        evidence=[
            EvidenceRef(
                path=rule.path, line_start=rule.line, line_end=rule.line, kind="code_ref", note="stated in project docs"
            )
        ],
        support_count=1,
        violation_count=0,
        rule_type="prose",
        severity="warn",
        detector="prose",
    )


def words(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower()):
        if len(w) > 2 and w not in _STOP:
            out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def relevance(a: str, b: str) -> float:
    wa, wb = words(a), words(b)
    if not wa or not wb:
        return 0.0
    shared = wa & wb
    return len(shared) / min(len(wa), len(wb)) if len(shared) >= 2 else 0.0
