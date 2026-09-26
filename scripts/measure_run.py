"""Measure an agent's run against Phase 8 guardrails to produce comparative metrics (Phase 9).

Usage:
    uv run python scripts/measure_run.py --repo <path> [--base <sha>] [--plan <plan.json>]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add src to sys.path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agent_factory.guardrails.diff import analyze_diff
from agent_factory.guardrails.rules import run_guardrails
from agent_factory.runtime import Runtime
from agent_factory.workflow.feature import FeaturePlan


def measure_worktree(repo_path: Path, base_sha: str | None = None, plan_path: Path | None = None) -> dict[str, int | float | str]:
    rt = Runtime.open(repo_path)
    plan = None
    if plan_path and plan_path.exists():
        plan = FeaturePlan.model_validate_json(plan_path.read_text(encoding="utf-8"))

    diff = analyze_diff(repo_path, base_sha=base_sha, project_id=rt.project_id)
    report = run_guardrails(diff, rt, plan=plan)

    dup_fails = sum(1 for c in report.checks if c.check == "duplication" and c.status == "fail")
    arch_fails = sum(1 for c in report.checks if c.check == "architecture" and c.status == "fail")
    complex_findings = sum(len(c.findings) for c in report.checks if c.check == "complexity")
    scope_findings = sum(len(c.findings) for c in report.checks if c.check == "scope")

    return {
        "status": report.status,
        "duplicate_abstractions": dup_fails,
        "constraint_violations": arch_fails,
        "new_dependencies": complex_findings,
        "out_of_scope_files": scope_findings,
        "lines_added": diff.total_added_lines,
        "lines_deleted": diff.total_deleted_lines,
        "total_findings": report.total_findings,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Measure an agent run with Agent Factory guardrails")
    parser.add_argument("--repo", type=Path, default=Path("."), help="Path to repo worktree")
    parser.add_argument("--base", type=str, default=None, help="Base commit SHA")
    parser.add_argument("--plan", type=Path, default=None, help="Path to plan JSON")
    args = parser.parse_args()

    metrics = measure_worktree(args.repo, args.base, args.plan)
    print(json.dumps(metrics, indent=2))
