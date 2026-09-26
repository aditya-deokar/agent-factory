"""KnowledgeService: the only write path for Patterns, Decisions and Constraints.

The auditor, ADR parsing, agents (MCP `propose_memory`, Phase 6) and humans
(`agent-factory memory ...`) all go through `submit()` or the explicit lifecycle
actions below, so validation, confidence, the lifecycle guard and the audit log
are applied the same way to every piece of memory.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..auditor.constraints import RULE_TYPES, check_cypher, evaluate
from ..auditor.graphview import GraphView
from ..config.model import ValidationConfig
from ..db.repos import KnowledgeRepo
from ..db.stores import Neo4jStore
from ..schema.model import KnowledgeKind, KnowledgeSource, KnowledgeStatus, Label, Rel
from ..schema.uids import knowledge_uid
from .audit_log import AuditLog
from .confidence import confidence as compute_confidence
from .lifecycle import IllegalTransition, check_transition
from .validation import (
    EvidenceRef,
    FileCache,
    Proposal,
    ValidationResult,
    actor_for,
    check_evidence,
    decide_status,
    jaccard,
    score,
    secret_findings,
    title_tokens,
)

MACHINE_CHECKABLE = set(RULE_TYPES)
DUPLICATE_TITLE_SIMILARITY = 0.8


def human_actor(root: Path | None) -> str:
    name = ""
    if root is not None:
        try:
            name = subprocess.run(
                ["git", "config", "user.name"], cwd=root, capture_output=True, text=True, check=False
            ).stdout.strip()
        except OSError:
            name = ""
    return f"human:{name or 'local'}"


class KnowledgeService:
    def __init__(
        self,
        store: Neo4jStore,
        project_id: str,
        root: Path,
        cfg: ValidationConfig | None = None,
        view: GraphView | None = None,
    ):
        self.store = store
        self.project_id = project_id
        self.root = root
        self.cfg = cfg or ValidationConfig()
        self.repo = KnowledgeRepo(store, project_id)
        self.log = AuditLog(self.repo, root)
        self.files = FileCache(root)
        self._view = view

    @property
    def view(self) -> GraphView:
        if self._view is None:
            self._view = GraphView.load(self.store, self.project_id)
        return self._view

    def uid_for(self, p: Proposal) -> str:
        return knowledge_uid(self.project_id, p.kind, p.key or p.title)

    # -- submit ------------------------------------------------------------------

    def submit(
        self, p: Proposal, actor: str | None = None, *, replace_evidence: bool | None = None
    ) -> ValidationResult:
        actor = actor or actor_for(p.source)
        uid = self.uid_for(p)
        secret_fields = secret_findings(p)
        if secret_fields:
            self.log.record(uid, "reject", actor, reason=f"contains_secret in {', '.join(secret_fields)}")
            return ValidationResult("rejected", None, None, reasons=[f"contains a secret ({', '.join(secret_fields)})"])
        kept, dropped = check_evidence(p.evidence, self.files)
        contra, dropped_contra = check_evidence(p.contradictions, self.files)
        existing = self.repo.get(uid)
        # Revalidating existing knowledge must be able to proceed when its evidence is gone (to deprecate it).
        revalidating = actor == "revalidation" and existing is not None
        if not kept and not (p.rule_type in MACHINE_CHECKABLE and p.rule) and not revalidating:
            self.log.record(uid, "reject", actor, reason="no_evidence", details={"dropped": dropped})
            return ValidationResult("rejected", None, None, reasons=["no valid evidence"], dropped_evidence=dropped)
        p = p.model_copy(update={"evidence": kept, "contradictions": contra})

        outcome = "updated" if existing else "created"
        merge = False
        if existing is None and p.source in (KnowledgeSource.AGENT, KnowledgeSource.HUMAN):
            dup = self.find_duplicate(p)
            if dup is not None:
                uid, existing, outcome, merge = dup["uid"], dup, "merged", True
        # Rejected knowledge is not re-suggested by agents, whether it matched by id or by similarity.
        if (
            existing is not None
            and existing.get("status") == KnowledgeStatus.REJECTED
            and p.source == KnowledgeSource.AGENT
        ):
            self.log.record(uid, "reject", actor, reason="previously_rejected")
            return ValidationResult(
                "rejected",
                uid,
                KnowledgeStatus.REJECTED.value,
                reasons=[f"a matching claim was rejected before ({uid})"],
            )

        if p.kind == KnowledgeKind.CONSTRAINT and p.rule_type in MACHINE_CHECKABLE and p.rule:
            p = self._rederive(p)
        elif merge and existing is not None:
            p = p.model_copy(
                update={
                    "support_count": int(existing.get("support_count") or 0) + len(kept),
                    "violation_count": int(existing.get("violation_count") or 0) + len(contra),
                }
            )

        human_ok = bool(existing and str(existing.get("validated_by", "")).startswith("human"))
        support, violations, conf = score(p, human_approved=human_ok)
        decision = decide_status(existing, p, support, conf, self.cfg)
        current = existing.get("status") if existing else None
        target = decision.status
        if current is not None:
            try:
                check_transition(current, target, actor, rederived=p.rederived)
            except IllegalTransition as error:
                target, decision.reason = KnowledgeStatus(current), f"kept {current}: {error}"

        now = datetime.now(UTC).isoformat()
        props: dict[str, Any] = {
            "uid": uid,
            "kind": p.kind.value,
            "title": p.title if not merge else existing["title"],  # type: ignore[index]
            "claim": p.claim if not merge else existing["claim"],  # type: ignore[index]
            "category": p.category,
            "source": p.source.value if not merge else existing.get("source", p.source.value),  # type: ignore[union-attr]
            "status": target.value,
            "confidence": conf,
            "support_count": support,
            "violation_count": violations,
            "rule_type": p.rule_type,
            "rule": json.dumps(p.rule, sort_keys=True) if p.rule else None,
            "severity": p.severity,
            "check_cypher": p.check_cypher,
            "detector": p.detector,
            "rationale": p.rationale,
            "adr_id": p.adr_id,
            "context": p.context,
            "consequences": p.consequences,
            "source_path": p.source_path,
            "status_reason": decision.reason or (existing.get("status_reason") if existing else None),
        }
        if target == KnowledgeStatus.VALIDATED and current != KnowledgeStatus.VALIDATED.value:
            props["validated_at"], props["validated_by"] = now, actor
        self.repo.upsert({k: v for k, v in props.items() if v is not None or k in ("status_reason",)})

        replace = (not merge) if replace_evidence is None else replace_evidence
        if replace:
            self.repo.clear_evidence(uid, Rel.SUPPORTED_BY)
            self.repo.clear_evidence(uid, Rel.CONTRADICTED_BY)
        self.repo.attach_evidence(uid, [_ev_row(e) for e in p.evidence], Rel.SUPPORTED_BY)
        if p.contradictions:
            self.repo.attach_evidence(uid, [_ev_row(e) for e in p.contradictions], Rel.CONTRADICTED_BY)

        changed = current != target.value
        if outcome == "created" or changed or merge:
            self.log.record(
                uid,
                {"created": "create", "merged": "merge"}.get(outcome, "status_change"),
                actor,
                from_status=current,
                to_status=target.value,
                reason=decision.reason,
                details={"confidence": conf, "support": support, "violations": violations},
            )
        return ValidationResult(
            outcome, uid, target.value, conf, [r for r in [decision.reason] if r], dropped + dropped_contra
        )

    def _rederive(self, p: Proposal) -> Proposal:
        assert p.rule_type and p.rule is not None
        ok, violations = evaluate(self.view, p.rule_type, p.rule)
        contra = []
        for violator, _target in violations[:20]:
            sym = self.view.symbols.get(violator)
            if sym is not None:
                contra.append(
                    EvidenceRef(
                        path=sym.path,
                        line_start=sym.line_start,
                        line_end=sym.line_end,
                        symbol_uid=sym.uid,
                        note=f"violates: {p.title}",
                    )
                )
            elif violator in {f["uid"] for f in self.view.files.values()}:
                contra.append(EvidenceRef(path=violator.split(":file:", 1)[1], note=f"violates: {p.title}"))
        contra, _ = check_evidence(contra, self.files)
        return p.model_copy(
            update={
                "support_count": len(ok),
                "violation_count": len({v for v, _ in violations}),
                "contradictions": contra or p.contradictions,
                "rederived": True,
                "check_cypher": p.check_cypher or check_cypher(p.rule_type, p.rule),
            }
        )

    def find_duplicate(self, p: Proposal) -> dict[str, Any] | None:
        tokens = title_tokens(f"{p.title}")
        rule_json = json.dumps(p.rule, sort_keys=True) if p.rule else None
        for item in self.repo.list_knowledge(kind=p.kind):
            if rule_json and item.get("rule") == rule_json:
                return item
            if jaccard(tokens, title_tokens(item.get("title", ""))) >= DUPLICATE_TITLE_SIMILARITY:
                return item
        return None

    def ingest(self, proposals: Iterable[Proposal]) -> list[ValidationResult]:
        return [self.submit(p) for p in proposals]

    # -- explicit lifecycle actions ------------------------------------------------

    def _transition(
        self, uid: str, target: KnowledgeStatus, actor: str, reason: str | None, action: str, **extra: Any
    ) -> dict[str, Any]:
        item = self.repo.get(uid)
        if item is None:
            raise KeyError(f"knowledge {uid} not found")
        check_transition(item["status"], target, actor)
        props: dict[str, Any] = {"status_reason": reason, **extra}
        if target == KnowledgeStatus.VALIDATED:
            props |= {"validated_at": datetime.now(UTC).isoformat(), "validated_by": actor}
            if actor.startswith("human"):
                props["confidence"] = compute_confidence(
                    int(item.get("support_count") or 0),
                    int(item.get("violation_count") or 0),
                    item.get("source", "human"),
                    human_approved=True,
                )
        previous = self.repo.set_status(uid, target, **{k: v for k, v in props.items() if v is not None})
        self.log.record(uid, action, actor, from_status=previous, to_status=target.value, reason=reason)
        return {**item, **props, "status": target.value}

    def approve(self, uid: str, actor: str, reason: str | None = None) -> dict[str, Any]:
        return self._transition(uid, KnowledgeStatus.VALIDATED, actor, reason, "approve")

    def reject(self, uid: str, actor: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("a reason is required to reject knowledge")
        return self._transition(uid, KnowledgeStatus.REJECTED, actor, reason, "reject")

    def deprecate(self, uid: str, actor: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("a reason is required to deprecate knowledge")
        return self._transition(uid, KnowledgeStatus.DEPRECATED, actor, reason, "deprecate")

    def supersede(self, uid: str, by_uid: str, actor: str, reason: str | None = None) -> dict[str, Any]:
        if self.repo.get(by_uid) is None:
            raise KeyError(f"replacement {by_uid} not found")
        result = self._transition(
            uid,
            KnowledgeStatus.SUPERSEDED,
            actor,
            reason or f"superseded by {by_uid}",
            "supersede",
            superseded_by=by_uid,
        )
        self.repo.link(by_uid, Rel.SUPERSEDES, uid, at=datetime.now(UTC).isoformat())
        return result

    def delete(self, uid: str, actor: str) -> bool:
        item = self.repo.get(uid)
        if item is None:
            raise KeyError(f"knowledge {uid} not found")
        self.repo.delete(uid)
        self.log.record(uid, "delete", actor, from_status=item.get("status"), reason="deleted on request")
        return True

    # -- links -------------------------------------------------------------------

    def link_symbols(self, uid: str, rel: Rel, symbol_uids: list[str]) -> int:
        """Replace incoming FOLLOWS / CONSTRAINED_BY edges from symbols to one knowledge item."""
        self.repo.unlink_all(uid, rel, incoming=True)
        rows = [{"src": s, "dst": uid} for s in sorted(set(symbol_uids))]
        return self.repo.link_many(rel, rows, Label.SYMBOL, Label.KNOWLEDGE) if rows else 0


def _ev_row(e: EvidenceRef) -> dict[str, Any]:
    return e.model_dump(exclude_none=True)
