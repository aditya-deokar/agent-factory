"""Revalidation after an audit (spec §20): stale architecture must not keep controlling new code.

Knowledge the current audit did not re-propose is re-checked:
- machine-checkable constraints are re-derived against the fresh graph;
- auditor patterns that were not detected again have lost their support;
- evidence-only knowledge (agent / human) has its evidence re-hashed; if all of it
  is gone or changed, the item is flagged stale (and deprecated if it was validated).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..schema.model import KnowledgeKind, KnowledgeSource, KnowledgeStatus
from .knowledge import MACHINE_CHECKABLE, KnowledgeService
from .lifecycle import IllegalTransition
from .validation import EvidenceRef, Proposal, evidence_hash

ACTIVE = (KnowledgeStatus.CANDIDATE, KnowledgeStatus.VALIDATED, KnowledgeStatus.DEPRECATED)


@dataclass
class RevalidationSummary:
    checked: int = 0
    deprecated: list[str] = field(default_factory=list)
    revalidated: list[str] = field(default_factory=list)
    stale_evidence: int = 0
    refreshed_evidence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "deprecated": self.deprecated,
            "revalidated": self.revalidated,
            "stale_evidence": self.stale_evidence,
            "refreshed_evidence": self.refreshed_evidence,
        }


def revalidate(service: KnowledgeService, touched: set[str]) -> RevalidationSummary:
    summary = RevalidationSummary()
    for item in service.repo.list_knowledge(limit=10_000):
        status = KnowledgeStatus(item["status"])
        if item["uid"] in touched or status not in ACTIVE:
            continue
        summary.checked += 1
        if item.get("rule_type") in MACHINE_CHECKABLE and item.get("rule"):
            _resubmit(service, item, summary, rule=json.loads(item["rule"]))
        elif item.get("source") == KnowledgeSource.AUDITOR.value and item.get("detector"):
            _resubmit(service, item, summary, lost_support=True)
        else:
            _check_evidence(service, item, summary)
    return summary


def _resubmit(
    service: KnowledgeService,
    item: dict[str, Any],
    summary: RevalidationSummary,
    *,
    rule: dict[str, Any] | None = None,
    lost_support: bool = False,
) -> None:
    evidence = [
        EvidenceRef(
            path=e["path"],
            line_start=e.get("line_start"),
            line_end=e.get("line_end"),
            symbol_uid=e.get("symbol_uid"),
            note=e.get("note"),
        )
        for e in service.repo.evidence_of(item["uid"])
        if e["rel"] == "SUPPORTED_BY" and e.get("path")
    ]
    proposal = Proposal(
        kind=KnowledgeKind(item["kind"]),
        title=item["title"],
        claim=item["claim"],
        source=KnowledgeSource(item.get("source", "auditor")),
        key=item["uid"].split(":", 3)[-1],
        category=item.get("category"),
        evidence=evidence,
        support_count=0 if lost_support else None,
        violation_count=0 if lost_support else None,
        rule_type=item.get("rule_type"),
        rule=rule,
        severity=item.get("severity"),
        detector=item.get("detector"),
        rationale=item.get("rationale"),
    )
    before = item["status"]
    try:
        result = service.submit(proposal, actor="revalidation", replace_evidence=False)
    except IllegalTransition:
        return
    if result.status == KnowledgeStatus.DEPRECATED.value and before != result.status:
        summary.deprecated.append(item["uid"])
    if result.status == KnowledgeStatus.VALIDATED.value and before == KnowledgeStatus.DEPRECATED.value:
        summary.revalidated.append(item["uid"])


def _check_evidence(service: KnowledgeService, item: dict[str, Any], summary: RevalidationSummary) -> None:
    refs = [e for e in service.repo.evidence_of(item["uid"]) if e["rel"] == "SUPPORTED_BY" and e.get("path")]
    if not refs:
        return
    stale = 0
    for e in refs:
        current = evidence_hash(e["path"], e.get("line_start"), e.get("line_end"), service.files)
        is_stale = current is None or (e.get("content_hash") and current != e["content_hash"])
        if is_stale:
            stale += 1
            if not e.get("stale"):
                service.repo.mark_evidence(
                    e["uid"], stale=True, stale_reason="missing" if current is None else "changed"
                )
                summary.stale_evidence += 1
        elif e.get("stale"):
            service.repo.mark_evidence(e["uid"], stale=False, stale_reason=None)
            summary.refreshed_evidence += 1
    if stale == len(refs) and item["status"] == KnowledgeStatus.VALIDATED.value:
        try:
            service.deprecate(item["uid"], "revalidation", "all supporting evidence is missing or changed")
            summary.deprecated.append(item["uid"])
        except IllegalTransition:
            pass
