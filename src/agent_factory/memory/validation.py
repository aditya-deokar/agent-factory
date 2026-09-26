"""Validation: nothing becomes memory without evidence (spec §14, §20).

Pipeline for every proposal (auditor, ADR, agent or human):
  1 schema  2 secrets  3 evidence exists (+ hash)  4 dedupe  5 re-derive rules
  6 confidence  7 status policy (auto-validate / keep candidate / deprecate)
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..common.paths import to_posix
from ..common.redact import contains_secret
from ..config.model import ValidationConfig
from ..schema.model import KnowledgeKind, KnowledgeSource, KnowledgeStatus
from .confidence import confidence as compute_confidence
from .lifecycle import actor_kind

_EVIDENCE_SPEC = re.compile(r"^(?P<path>[^:]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")
MAX_EVIDENCE = 20


class EvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    symbol_uid: str | None = None
    note: str | None = Field(default=None, max_length=300)
    kind: str = "code_ref"
    content_hash: str | None = None
    commit: str | None = None

    @field_validator("path")
    @classmethod
    def _posix(cls, v: str) -> str:
        return to_posix(v)

    @classmethod
    def parse(cls, spec: str, note: str | None = None) -> EvidenceRef:
        """`src/a.ts`, `src/a.ts:12` or `src/a.ts:12-40`."""
        m = _EVIDENCE_SPEC.match(spec.strip())
        if not m:
            raise ValueError(f"bad evidence reference {spec!r}; expected path[:start[-end]]")
        start = int(m.group("start")) if m.group("start") else None
        end = int(m.group("end")) if m.group("end") else start
        return cls(path=m.group("path"), line_start=start, line_end=end, note=note)


class Proposal(BaseModel):
    """A claim that should become project memory."""

    model_config = ConfigDict(extra="forbid")

    kind: KnowledgeKind
    title: str = Field(min_length=3, max_length=120)
    claim: str = Field(min_length=3, max_length=1000)
    source: KnowledgeSource
    key: str | None = None  # deterministic identity (detector key, ADR id); default: slug of title
    category: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    contradictions: list[EvidenceRef] = Field(default_factory=list)
    support_count: int | None = Field(default=None, ge=0)  # full counts when evidence is a sample
    violation_count: int | None = Field(default=None, ge=0)
    rederived: bool = False  # counts come from re-deriving the claim against the code graph
    rule_type: str | None = None
    rule: dict[str, Any] | None = None
    severity: str | None = None
    check_cypher: str | None = None
    detector: str | None = None
    rationale: str | None = Field(default=None, max_length=1000)
    # ADR-specific
    adr_id: str | None = None
    adr_status: KnowledgeStatus | None = None
    context: str | None = None
    consequences: str | None = None
    source_path: str | None = None


@dataclass
class ValidationResult:
    outcome: str  # created | updated | merged | rejected
    uid: str | None
    status: str | None
    confidence: float | None = None
    reasons: list[str] = field(default_factory=list)
    dropped_evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "uid": self.uid,
            "status": self.status,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "dropped_evidence": self.dropped_evidence,
        }


class FileCache:
    """Reads repository files once per validation run."""

    def __init__(self, root: Path):
        self.root = root
        self._lines: dict[str, list[str] | None] = {}

    def lines(self, path: str) -> list[str] | None:
        if path not in self._lines:
            target = self.root / path
            try:
                text = target.read_text(encoding="utf-8", errors="replace") if target.is_file() else None
            except OSError:
                text = None
            self._lines[path] = text.replace("\r\n", "\n").split("\n") if text is not None else None
        return self._lines[path]


def secret_findings(p: Proposal) -> list[str]:
    fields = {"title": p.title, "claim": p.claim, "rationale": p.rationale or "", "context": p.context or ""}
    fields |= {f"evidence[{i}].note": e.note or "" for i, e in enumerate(p.evidence)}
    return [name for name, value in fields.items() if value and contains_secret(value)]


def check_evidence(refs: list[EvidenceRef], files: FileCache) -> tuple[list[EvidenceRef], list[str]]:
    """Keep refs whose file exists and whose line range fits; stamp a content hash. -> (kept, dropped reasons)"""
    kept, dropped = [], []
    for ref in refs[:MAX_EVIDENCE]:
        lines = files.lines(ref.path)
        if lines is None:
            dropped.append(f"{ref.path}: file not found")
            continue
        start = ref.line_start or 1
        end = ref.line_end or (ref.line_start or len(lines))
        if start > end or end > len(lines):
            dropped.append(f"{ref.path}:{start}-{end}: outside the file ({len(lines)} lines)")
            continue
        body = "\n".join(lines[start - 1 : end])
        ref = ref.model_copy(update={"content_hash": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]})
        kept.append(ref)
    if len(refs) > MAX_EVIDENCE:
        dropped.append(f"{len(refs) - MAX_EVIDENCE} references beyond the first {MAX_EVIDENCE} were ignored")
    return kept, dropped


def evidence_hash(ref_path: str, line_start: int | None, line_end: int | None, files: FileCache) -> str | None:
    lines = files.lines(ref_path)
    if lines is None:
        return None
    start = line_start or 1
    end = line_end or len(lines)
    if end > len(lines) or start > end:
        return None
    return hashlib.sha256("\n".join(lines[start - 1 : end]).encode("utf-8")).hexdigest()[:16]


@dataclass
class StatusDecision:
    status: KnowledgeStatus
    reason: str | None


def score(p: Proposal, *, stale: bool = False, human_approved: bool = False) -> tuple[int, int, float]:
    support = p.support_count if p.support_count is not None else len(p.evidence)
    violations = p.violation_count if p.violation_count is not None else len(p.contradictions)
    # A claim re-derived from the code graph is weighed like the auditor's own findings, whoever proposed it.
    weight_as = KnowledgeSource.AUDITOR if p.rederived and p.source == KnowledgeSource.AGENT else p.source
    conf = compute_confidence(
        support,
        violations,
        weight_as,
        stale=stale,
        adr_accepted=p.source == KnowledgeSource.ADR and p.adr_status == KnowledgeStatus.VALIDATED,
        human_approved=human_approved,
    )
    return support, violations, conf


def decide_status(
    existing: dict[str, Any] | None,
    p: Proposal,
    support: int,
    conf: float,
    cfg: ValidationConfig,
) -> StatusDecision:
    """The status policy. Guards against illegal moves live in lifecycle.check_transition."""
    current = KnowledgeStatus(existing["status"]) if existing and existing.get("status") else None
    if p.source == KnowledgeSource.ADR and p.adr_status is not None:
        return StatusDecision(p.adr_status, f"ADR status: {p.adr_status.value}")
    if current in (KnowledgeStatus.REJECTED, KnowledgeStatus.SUPERSEDED):
        return StatusDecision(current, None)
    trusted = p.source in (KnowledgeSource.AUDITOR, KnowledgeSource.ADR, KnowledgeSource.HUMAN) or p.rederived
    passes = trusted and conf >= cfg.auto_validate_min_confidence and support >= cfg.auto_validate_min_support
    human_validated = bool(existing and str(existing.get("validated_by", "")).startswith("human"))
    if current is None or current == KnowledgeStatus.CANDIDATE:
        if passes:
            return StatusDecision(KnowledgeStatus.VALIDATED, f"auto-validated: confidence {conf}, support {support}")
        return StatusDecision(KnowledgeStatus.CANDIDATE, None)
    if current == KnowledgeStatus.VALIDATED:
        if human_validated:
            if support < cfg.auto_validate_min_support:
                return StatusDecision(KnowledgeStatus.DEPRECATED, f"support dropped to {support}")
            return StatusDecision(current, None)
        if not passes and trusted:
            prior = existing.get("support_count") if existing else None
            return StatusDecision(KnowledgeStatus.DEPRECATED, f"support dropped: {prior}→{support}, confidence {conf}")
        return StatusDecision(current, None)
    if current == KnowledgeStatus.DEPRECATED and (
        passes or (human_validated and support >= cfg.auto_validate_min_support)
    ):
        return StatusDecision(KnowledgeStatus.VALIDATED, f"re-observed: confidence {conf}, support {support}")
    return StatusDecision(current, None)


def title_tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def actor_for(source: KnowledgeSource) -> str:
    return {"auditor": "auditor", "adr": "adr", "agent": "agent", "human": "human"}[source.value]


__all__ = [
    "EvidenceRef",
    "FileCache",
    "Proposal",
    "StatusDecision",
    "ValidationResult",
    "actor_for",
    "actor_kind",
    "check_evidence",
    "decide_status",
    "evidence_hash",
    "jaccard",
    "score",
    "secret_findings",
    "title_tokens",
]
