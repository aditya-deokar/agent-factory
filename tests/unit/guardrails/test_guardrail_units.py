"""Unit tests for the 8 anti-slop guardrail checks (Phase 8, crafted DiffFragments, no DB)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.auditor.graphview import GraphView, Sym
from agent_factory.auditor.model import NodeRow
from agent_factory.config.model import ChecksConfig
from agent_factory.context.reuse import ProposedAbstraction, ReuseCandidate, ReuseReport
from agent_factory.guardrails.checks.abstraction import check_abstraction
from agent_factory.guardrails.checks.architecture import check_architecture
from agent_factory.guardrails.checks.complexity import check_complexity
from agent_factory.guardrails.checks.consistency import check_consistency
from agent_factory.guardrails.checks.duplication import check_duplication
from agent_factory.guardrails.checks.regression import check_regression
from agent_factory.guardrails.checks.reusability import check_reusability
from agent_factory.guardrails.checks.scope import check_scope
from agent_factory.guardrails.diff import DiffFile, DiffFragment
from agent_factory.workflow.feature import (
    FeaturePlan,
    IllegalFeatureTransition,
    NewAbstraction,
    check_feature_transition,
)


class FakeReuseDetector:
    def __init__(self, score_map: dict[str, float]):
        self.score_map = score_map

    def find(self, proposed: ProposedAbstraction) -> ReuseReport:
        score = self.score_map.get(proposed.name, 0.2)
        top = (
            ReuseCandidate(
                uid="proj:s:token-service",
                name="TokenService",
                kind="class",
                path="src/services/token.service.ts",
                score=score,
                verdict="reuse" if score >= 0.7 else "new_ok",
            )
            if score >= 0.5
            else None
        )
        return ReuseReport(
            proposed=proposed,
            verdict="reuse" if score >= 0.7 else "new_ok",
            candidates=[top] if top else [],
        )


def test_duplication_fails_without_plan_justification():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        added_symbols=[
            NodeRow(
                uid="test:s:inv-token",
                props={"name": "InvitationTokenService", "kind": "class", "path": "src/services/invite.ts"},
                roles=["Service"],
            )
        ],
    )
    reuse = FakeReuseDetector({"InvitationTokenService": 0.88})
    res = check_duplication(diff, reuse, plan=FeaturePlan())
    assert res.status == "fail"
    assert any("duplicates existing 'TokenService'" in f.message for f in res.findings)


def test_duplication_passes_with_plan_justification():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        added_symbols=[
            NodeRow(
                uid="test:s:inv-token",
                props={"name": "InvitationTokenService", "kind": "class", "path": "src/services/invite.ts"},
                roles=["Service"],
            )
        ],
    )
    plan = FeaturePlan(
        new_abstractions=[
            NewAbstraction(
                name="InvitationTokenService",
                justification="Isolated cryptographic requirements for multi-tenant invitations",
            )
        ]
    )
    reuse = FakeReuseDetector({"InvitationTokenService": 0.88})
    res = check_duplication(diff, reuse, plan=plan)
    assert res.status == "warn"  # Waived due to justification
    assert res.findings[0].waived is True


def test_abstraction_warns_on_undeclared_or_excessive():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        added_symbols=[
            NodeRow(uid=f"s:{i}", props={"name": f"Service{i}", "kind": "class"}, roles=["Service"]) for i in range(5)
        ],
    )
    res = check_abstraction(diff, plan=FeaturePlan(), max_abstractions=3)
    assert res.status == "warn"
    assert any("limit is 3" in f.message for f in res.findings)


def test_architecture_fails_on_forbidden_dependency():
    view = GraphView("test")
    view.symbols["c:controller"] = Sym(
        "c:controller", "InviteController", "class", "src/controllers/invite.ts", {"Controller"}
    )
    view.symbols["r:repo"] = Sym("r:repo", "TeamRepository", "class", "src/repos/team.ts", {"Repository"})
    view.add_edge("USES", "c:controller", "r:repo")

    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        added_symbols=[
            NodeRow(
                uid="c:controller",
                props={"name": "InviteController", "path": "src/controllers/invite.ts"},
                roles=["Controller"],
            )
        ],
        overlay_view=view,
    )

    constraint = {
        "uid": "test:k:c1",
        "title": "ADR-002: No DB in controllers",
        "rule_type": "forbid_dependency",
        "rule": {"from_role": "Controller", "to_roles": ["Repository", "Model"]},
        "claim": "Controllers must use services, not repositories.",
        "severity": "fail",
    }

    res = check_architecture(diff, store=None, project_id="test", validated_constraints=[constraint])
    assert res.status == "fail"
    assert any("violates rule 'forbid_dependency'" in f.message for f in res.findings)


def test_reusability_warns_on_bloated_controller_or_unexported_service():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        added_symbols=[
            NodeRow(
                uid="s:ctrl",
                props={"name": "postInvite", "kind": "method", "loc": 55, "path": "src/controllers/invite.ts"},
                roles=["Controller"],
            ),
            NodeRow(
                uid="s:svc",
                props={
                    "name": "SecretService",
                    "kind": "class",
                    "loc": 10,
                    "exported": False,
                    "path": "src/services/secret.ts",
                },
                roles=["Service"],
            ),
        ],
    )
    res = check_reusability(diff)
    assert res.status == "warn"
    assert len(res.findings) == 2


def test_consistency_warns_on_unvalidated_route_or_bad_naming():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        added_symbols=[
            NodeRow(
                uid="s:r1",
                props={"name": "GET /teams/invite", "kind": "route", "validated": False, "path": "src/routes/teams.ts"},
                roles=["Route"],
            ),
            NodeRow(
                uid="s:s1",
                props={"name": "InviteWorkerHelper", "kind": "class", "path": "src/services/helper.ts"},
                roles=["Service"],
            ),
        ],
    )
    res = check_consistency(diff)
    assert res.status == "warn"
    assert any("validation schema" in f.message for f in res.findings)
    assert any("naming convention" in f.message for f in res.findings)


def test_complexity_fails_on_duplicate_category_dependency():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        new_external_deps={"zustand": "package.json"},
    )
    existing_deps = {"redux": "package.json", "express": "package.json"}
    res = check_complexity(diff, existing_deps=existing_deps)
    assert res.status == "fail"
    assert any("Introduced duplicate dependency category 'state-management'" in f.message for f in res.findings)


def test_scope_fails_when_drift_exceeds_threshold():
    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        files=[
            DiffFile(path="src/services/invite.service.ts", status="A", added_lines=50, deleted_lines=0),
            DiffFile(path="src/legacy/billing.ts", status="M", added_lines=150, deleted_lines=50),
        ],
        total_added_lines=200,
        total_deleted_lines=50,
    )
    plan = FeaturePlan(
        planned_files=["src/services/invite.service.ts"],
        planned_modules=["src/services"],
    )
    res = check_scope(diff, plan)
    assert res.status == "fail"
    assert any("Scope drift" in f.message for f in res.findings)


def test_regression_runner_fails_on_exit_code_or_count_drop():
    def fake_failing_runner(cmd: str, cwd: Path) -> tuple[int, str]:
        return 1, "Tests: 10 failed, 5 passed"

    cfg = ChecksConfig(test="npm test", build=None, lint=None)
    res = check_regression(Path("."), checks_config=cfg, runner=fake_failing_runner)
    assert res.status == "fail"
    assert any("Regression check 'test' failed" in f.message for f in res.findings)

    # Test count drop
    def fake_drop_runner(cmd: str, cwd: Path) -> tuple[int, str]:
        return 0, "15 passed"

    res_drop = check_regression(Path("."), checks_config=cfg, test_baseline=20, runner=fake_drop_runner)
    assert res_drop.status == "fail"
    assert any("dropped from baseline of 20 to 15" in f.message for f in res_drop.findings)


def test_feature_state_machine():
    check_feature_transition("planning", "in_progress")
    check_feature_transition("in_progress", "verifying")
    check_feature_transition("verifying", "done")
    with pytest.raises(IllegalFeatureTransition):
        check_feature_transition("done", "in_progress")
    with pytest.raises(IllegalFeatureTransition):
        check_feature_transition("abandoned", "verifying")
