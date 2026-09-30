"""Memory commit (spec §25): commits completed features into domain and agent memory.

Runs incremental audit for changed files, links Feature edges (MODIFIES, INTRODUCES,
REUSES, TESTED_BY, HAS_EVIDENCE), revalidates knowledge, saves agent facts, and closes traces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ..auditor.pipeline import AuditOptions, run_audit
from ..evidence.store import EvidenceStore
from ..guardrails.diff import analyze_diff
from ..memory.agent_memory import feature_session
from ..memory.validation import EvidenceRef, Proposal
from ..schema.model import FeatureStatus, KnowledgeKind, KnowledgeSource, Rel
from .feature import FeatureNotFound, check_feature_transition

if TYPE_CHECKING:
    from ..runtime import Runtime


async def complete_feature(
    rt: Runtime,
    fid: str | None = None,
    outcome: str = "success",
    pr_url: str | None = None,
    pre_merge: bool = True,
) -> dict[str, Any]:
    """Complete a feature session and commit memory into the graph."""
    service = rt.features()
    state = service.active(fid)
    if state is None:
        raise FeatureNotFound("no active feature to complete on this branch")

    target_status = FeatureStatus.DONE.value if outcome == "success" else FeatureStatus.ABANDONED.value
    check_feature_transition(state.status, target_status)

    memory_diff: dict[str, Any] = {
        "feature_id": state.feature_id,
        "outcome": outcome,
        "added_symbols": 0,
        "modified_symbols": 0,
        "edges_created": 0,
        "reused_symbols": [],
        "candidates_promoted": 0,
    }

    if outcome == "success":
        # 1. Analyze final diff
        diff = analyze_diff(
            rt.root,
            base_sha=state.base_sha,
            project_id=rt.project_id,
            stored_view=None,
        )

        changed_paths = list(diff.changed_paths())

        # 2. Run incremental audit into domain graph
        try:
            audit_result = run_audit(
                rt.root,
                rt.config,
                rt.stores,
                AuditOptions(),
                embedder=rt.embedder,
            )
            memory_diff["audit_run_uid"] = audit_result.run_uid
        except Exception as err:
            rt.warnings.append(f"incremental audit warning: {err}")

        # 3. Link Feature edges in graph
        feat_uid = state.uid
        store = rt.store

        # INTRODUCES
        added_uids = [s.uid for s in diff.added_symbols]
        if added_uids:
            service.repo.link(feat_uid, Rel.INTRODUCES, added_uids)
            memory_diff["added_symbols"] = len(added_uids)

        # MODIFIES
        mod_uids = [s.uid for s in diff.modified_symbols]
        if mod_uids:
            service.repo.link(feat_uid, Rel.MODIFIES, mod_uids)
            memory_diff["modified_symbols"] = len(mod_uids)

        # REUSES
        reused_names = []
        if state.plan:
            for d in state.plan.reuse_decisions:
                if d.chosen and d.verdict in ("reuse", "extend"):
                    reused_names.append(d.chosen)
        if reused_names:
            rows = store.read(
                """
                MATCH (s:Symbol {project_id: $p})
                WHERE s.name IN $names
                RETURN s.uid AS uid
                """,
                p=rt.project_id,
                names=reused_names,
            )
            reused_uids = [r["uid"] for r in rows]
            if reused_uids:
                service.repo.link(feat_uid, Rel.REUSES, reused_uids)
                memory_diff["reused_symbols"] = reused_names

        # TESTED_BY
        test_files = [f.path for f in diff.files if f.is_test]
        if test_files:
            test_file_uids = [f"{rt.project_id}:file:{p}" for p in test_files]
            service.repo.link(feat_uid, Rel.TESTED_BY, test_file_uids)

        # HAS_EVIDENCE
        ev_store = EvidenceStore(rt.root, state.feature_id)
        ev_count = ev_store.sync_to_graph(store, rt.project_id)
        memory_diff["evidence_items"] = ev_count

        # 4. Propose ADR Decision if planned
        if state.plan and state.plan.architectural_decision:
            try:
                dec_proposal = Proposal(
                    kind=KnowledgeKind.DECISION,
                    title=f"Decision for {state.name}",
                    claim=state.plan.architectural_decision,
                    source=KnowledgeSource.AGENT,
                    evidence=[EvidenceRef(path=p) for p in changed_paths[:5]],
                )
                prop_res = rt.knowledge().submit(dec_proposal, actor="agent:memory_commit")
                if prop_res.uid:
                    service.repo.link(feat_uid, "CREATED", [prop_res.uid])
                    memory_diff["candidates_promoted"] += 1
            except Exception as e:
                rt.warnings.append(f"could not record decision candidate: {e}")

        # 5. Agent memory trace completion & fact storage
        if state.trace_id and rt.memory:
            try:
                await rt.memory.complete_trace(
                    state.trace_id,
                    outcome=f"Feature '{state.name}' completed with {len(changed_paths)} files changed.",
                    success=True,
                )
                for d in state.plan.reuse_decisions if state.plan else []:
                    if d.chosen:
                        await rt.memory.save_fact(
                            d.proposed,
                            "reuses",
                            d.chosen,
                            session_id=feature_session(state.uid),
                        )
            except Exception as e:
                rt.warnings.append(f"agent memory complete warning: {e}")

    # Finalize state
    state.outcome = outcome
    state.pr_url = pr_url
    state.completed_at = datetime.now(UTC).isoformat()
    state.memory_commit = memory_diff
    service.set_status(state, target_status, outcome=outcome, pr_url=pr_url)

    return {
        "status": target_status,
        "feature_id": state.feature_id,
        "summary": f"Feature {state.feature_id} completed ({outcome}).",
        "memory_commit": memory_diff,
    }
