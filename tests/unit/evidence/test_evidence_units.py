"""Unit tests for evidence store and PR body renderer (Phase 8)."""

from __future__ import annotations

from pathlib import Path

from agent_factory.evidence.pr_body import render_pr_body
from agent_factory.evidence.store import EvidenceStore
from agent_factory.guardrails.diff import DiffFragment
from agent_factory.guardrails.model import CheckResult, GuardrailReport
from agent_factory.workflow.feature import FeaturePlan, FeatureState


def test_evidence_verify_detects_tamper(tmp_path: Path):
    store = EvidenceStore(tmp_path, "feat_test")
    test_file = tmp_path / "output.txt"
    test_file.write_text("All 45 tests passed clean.", encoding="utf-8")

    store.add_artifact("test", test_file, "Test execution log", rel_dest="tests/stdout.txt")
    ok, errors = store.verify()

    assert ok is True
    assert len(errors) == 0

    # Tamper with file
    dest_file = store.dir / "tests" / "stdout.txt"
    dest_file.write_text("TAMPERED CONTENT", encoding="utf-8")

    ok_after, errors_after = store.verify()
    assert ok_after is False
    assert any("Evidence hash mismatch" in err for err in errors_after)


def test_pr_body_never_claims_missing_evidence(tmp_path: Path):
    store = EvidenceStore(tmp_path, "feat_test")
    # Only register a diff, no test, no recording
    diff_file = tmp_path / "stat.txt"
    diff_file.write_text("3 files changed, +40 lines", encoding="utf-8")
    store.add_artifact("diff", diff_file, "Diff stat", rel_dest="diff/stat.txt")

    state = FeatureState(
        feature_id="feat_test",
        uid="proj:feat:test",
        name="Team Invitations",
        request="Add team invitations",
        created_at="2026-09-26T12:00:00Z",
        plan=FeaturePlan(summary="Reused TokenService for invitation tokens"),
    )

    diff = DiffFragment(
        project_id="test",
        base_sha="abc",
        head_sha="def",
        total_added_lines=40,
        total_deleted_lines=0,
    )

    report = GuardrailReport.from_checks(
        "test",
        [
            CheckResult(check="duplication", status="pass", summary="Clean"),
            CheckResult(check="architecture", status="pass", summary="Clean"),
        ],
        feature_id="feat_test",
    )

    body = render_pr_body(state, diff, store, guardrail_report=report)

    # Must contain verified for diff
    assert "| `diff` |" in body
    assert "✓ Verified" in body

    # Must contain ✗ Missing for kinds not in store
    assert "✗ Missing" in body
    assert "| `recording` | - | ✗ Missing |" in body
    assert "| `screenshot` | - | ✗ Missing |" in body
