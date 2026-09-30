"""Guardrail 7: Scope & Drift (spec §22).

Checks:
- Changed files outside planned_files ∪ planned_modules ∪ their tests -> warn.
- More than 30% of changed lines out of scope -> fail.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING

from ...workflow.feature import FeaturePlan
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ..diff import DiffFragment

MAX_OUT_OF_SCOPE_LINE_RATIO = 0.30


def _is_in_scope(path: str, planned_files: set[str], planned_modules: set[str]) -> bool:
    if path in planned_files:
        return True
    # Test files corresponding to planned files
    for pf in planned_files:
        stem = posixpath.splitext(posixpath.basename(pf))[0]
        if stem and stem in path and ("test" in path or "spec" in path):
            return True
    # Check planned modules
    for pm in planned_modules:
        if path.startswith(f"{pm}/") or path == pm:
            return True
    # Standard metadata/test configuration files
    return path in ("package.json", "tsconfig.json", "pyproject.toml")


def check_scope(diff: DiffFragment, plan: FeaturePlan | None = None) -> CheckResult:
    check_name = "scope"
    if not plan or (not plan.planned_files and not plan.planned_modules):
        return CheckResult(
            check=check_name,
            status="pass",
            summary="No planned scope specified; scope check skipped.",
        )

    planned_files = set(plan.planned_files)
    planned_modules = set(plan.planned_modules)

    findings: list[Finding] = []
    out_of_scope_lines = 0
    total_lines = max(1, diff.total_added_lines + diff.total_deleted_lines)

    for f in diff.files:
        if not _is_in_scope(f.path, planned_files, planned_modules):
            file_lines = f.added_lines + f.deleted_lines
            out_of_scope_lines += file_lines
            findings.append(
                Finding(
                    id=f"scope-unplanned-{f.path}",
                    check=check_name,
                    severity="warn",
                    message=f"File '{f.path}' changed but was not listed in planned files or modules.",
                    path=f.path,
                    fix_hint="Add this file to plan.planned_files or revert unintended changes.",
                )
            )

    out_of_scope_ratio = out_of_scope_lines / total_lines
    if out_of_scope_ratio > MAX_OUT_OF_SCOPE_LINE_RATIO:
        findings.append(
            Finding(
                id="scope-drift-ratio",
                check=check_name,
                severity="fail",
                message=(
                    f"Scope drift: {out_of_scope_ratio:.1%} of changed lines ({out_of_scope_lines}/{total_lines}) "
                    f"are outside the planned scope (threshold is {MAX_OUT_OF_SCOPE_LINE_RATIO:.0%})."
                ),
                fix_hint="Update the plan to explicitly incorporate these changes or restrict scope.",
            )
        )

    has_fail = any(f.severity == "fail" and not f.waived for f in findings)
    status = "fail" if has_fail else ("warn" if findings else "pass")
    summary = (
        "All changes are within planned scope."
        if status == "pass"
        else f"{len(findings)} scope drift finding(s) ({out_of_scope_ratio:.1%} of lines out of scope)."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
        metadata={"out_of_scope_ratio": round(out_of_scope_ratio, 3)},
    )
