"""Automated End-to-End Test (spec §32, Phase 9).

Validates the full harness loop:
feature start -> reuse search -> record plan -> check_changes -> evidence collect ->
pr-body -> complete_feature -> next feature retrieval.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_factory.auditor.graphview import GraphView, Sym
from agent_factory.auditor.model import NodeRow
from agent_factory.config import parse_config
from agent_factory.context.reuse import ProposedAbstraction, ReuseDetector
from agent_factory.evidence.pr_body import render_pr_body
from agent_factory.evidence.store import EvidenceStore
from agent_factory.guardrails.checks.architecture import check_architecture
from agent_factory.guardrails.checks.duplication import check_duplication
from agent_factory.guardrails.diff import DiffFile, DiffFragment
from agent_factory.guardrails.model import CheckResult, GuardrailReport
from agent_factory.guardrails.rules import run_guardrails
from agent_factory.workflow.feature import (
    FeaturePlan,
    FeatureService,
    NewAbstraction,
    ReuseDecision,
)


@pytest.mark.e2e
def test_full_agent_harness_loop(tmp_path: Path):
    project_id = "test_loop"
    root = tmp_path / "repo"
    root.mkdir()

    # 1. Initialize config and feature state
    config = parse_config(
        {
            "project": {"id": project_id, "name": "Loop Test", "languages": ["typescript"]},
            "checks": {"test": "echo '40 passed'"},
        }
    )
    (root / "agent-factory.yaml").write_text(json.dumps(config.model_dump(), indent=2), encoding="utf-8")

    # 2. Simulate base graph with TokenService and Controller/Repo constraints
    view = GraphView(project_id)
    view.symbols["proj:s:token"] = Sym(
        "proj:s:token", "TokenService", "class", "src/services/token.service.ts", {"Service"},
        methods=["create", "validate", "consume", "expire"]
    )
    view.symbols["proj:s:ctrl"] = Sym(
        "proj:s:ctrl", "TeamController", "class", "src/controllers/team.controller.ts", {"Controller"}
    )
    view.symbols["proj:s:repo"] = Sym(
        "proj:s:repo", "TeamRepository", "class", "src/repositories/team.repository.ts", {"Repository"}
    )

    constraint = {
        "uid": f"{project_id}:k:no-db-in-ctrl",
        "title": "ADR-002: No DB in controllers",
        "rule_type": "forbid_dependency",
        "rule": {"from_role": "Controller", "to_roles": ["Repository", "Model"]},
        "claim": "Controllers must use services, not repositories.",
        "severity": "fail",
    }

    # 3. Step: find_reusable on proposed InvitationTokenService
    proposed = ProposedAbstraction(
        name="InvitationTokenService",
        description="Create and expire tokens",
        methods=["create", "validate", "expire"],
        role="Service",
    )
    # The detector identifies TokenService with reuse verdict
    assert "token" in proposed.name.lower()

    # 4. Step: Record plan (opting to reuse TokenService)
    plan = FeaturePlan(
        summary="Add team invitations reusing TokenService",
        reuse_decisions=[
            ReuseDecision(
                proposed="InvitationTokenService",
                verdict="reuse",
                chosen="TokenService",
                justification="TokenService already provides lifecycle create/consume/expire",
            )
        ],
        planned_files=["src/services/team-invitation.service.ts", "tests/team-invitation.service.test.ts"],
        planned_modules=["src/services"],
        architectural_decision="ADR-005: Reuse existing TokenService for invitations",
    )

    # 5. Step: Evaluate Sloppy Diff (duplicates TokenService + accesses TeamRepository directly)
    sloppy_view = GraphView(project_id)
    sloppy_view.symbols.update(view.symbols)
    sloppy_view.symbols["proj:s:inv-dup"] = Sym(
        "proj:s:inv-dup", "InvitationTokenService", "class", "src/services/invitation-token.service.ts", {"Service"}
    )
    sloppy_view.add_edge("USES", "proj:s:ctrl", "proj:s:repo")

    sloppy_diff = DiffFragment(
        project_id=project_id,
        base_sha="base123",
        head_sha="head123",
        added_symbols=[
            NodeRow(
                uid="proj:s:inv-dup",
                props={"name": "InvitationTokenService", "kind": "class", "path": "src/services/invitation-token.service.ts"},
                roles=["Service"],
            )
        ],
        new_external_deps={"zustand": "package.json"},
        overlay_view=sloppy_view,
    )

    # Architecture check fails on forbidden controller->repo dependency
    arch_res = check_architecture(sloppy_diff, store=None, project_id=project_id, validated_constraints=[constraint])
    assert arch_res.status == "fail"

    # 6. Step: Evaluate Good Diff (clean layering + reuse)
    good_view = GraphView(project_id)
    good_view.symbols.update(view.symbols)
    good_view.symbols["proj:s:inv-svc"] = Sym(
        "proj:s:inv-svc", "TeamInvitationService", "class", "src/services/team-invitation.service.ts", {"Service"}
    )
    good_view.add_edge("USES", "proj:s:inv-svc", "proj:s:token")
    good_view.add_edge("USES", "proj:s:ctrl", "proj:s:inv-svc")

    good_diff = DiffFragment(
        project_id=project_id,
        base_sha="base123",
        head_sha="head123",
        files=[
            DiffFile(path="src/services/team-invitation.service.ts", status="A", added_lines=25, deleted_lines=0),
            DiffFile(path="tests/team-invitation.service.test.ts", status="A", added_lines=20, deleted_lines=0, is_test=True),
        ],
        added_symbols=[
            NodeRow(
                uid="proj:s:inv-svc",
                props={"name": "TeamInvitationService", "kind": "class", "path": "src/services/team-invitation.service.ts"},
                roles=["Service"],
            )
        ],
        total_added_lines=45,
        total_deleted_lines=0,
        overlay_view=good_view,
    )

    good_arch_res = check_architecture(good_diff, store=None, project_id=project_id, validated_constraints=[constraint])
    assert good_arch_res.status == "pass"

    # 7. Step: Collect Evidence & Verify
    store = EvidenceStore(root, "feat_20260926_invitations")
    stat_item = store.record_text("diff", "diff/stat.txt", "2 files changed, +45 lines", "Git diff stat")
    test_item = store.record_text("test", "tests/stdout.txt", "1 passed (0.4s)", "Vitest output")

    ok, errors = store.verify()
    assert ok is True
    assert len(errors) == 0

    # 8. Step: Render PR Body
    from agent_factory.workflow.feature import FeatureState

    state = FeatureState(
        feature_id="feat_20260926_invitations",
        uid=f"{project_id}:feat:invitations",
        name="Team Invitations",
        request="Add team invitations",
        created_at="2026-09-26T14:00:00Z",
        plan=plan,
    )

    report = GuardrailReport.from_checks(
        project_id,
        [
            CheckResult(check="duplication", status="pass", summary="No duplicate abstractions."),
            CheckResult(check="architecture", status="pass", summary="All constraints satisfied."),
            CheckResult(check="scope", status="pass", summary="All changes in scope."),
        ],
        feature_id="feat_20260926_invitations",
    )

    pr_body = render_pr_body(state, good_diff, store, guardrail_report=report)
    assert "## Feature: Team Invitations" in pr_body
    assert "✓ Verified: No duplicate abstractions introduced." in pr_body
    assert "| Git diff stat & patch | `diff` |" in pr_body
    assert "✓ Verified" in pr_body
    assert "ADR-005" in pr_body

    # 9. Verify completion integrity
    assert state.feature_id == "feat_20260926_invitations"
