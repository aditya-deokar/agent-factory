"""Guardrail 2: Abstraction (spec §22).

Added role-bearing symbols not declared in the plan -> warn.
More than N (default 3) new abstractions -> warn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...workflow.feature import FeaturePlan
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ..diff import DiffFragment

MAX_NEW_ABSTRACTIONS = 3


def check_abstraction(
    diff: DiffFragment,
    plan: FeaturePlan | None = None,
    max_abstractions: int = MAX_NEW_ABSTRACTIONS,
) -> CheckResult:
    check_name = "abstraction"
    planned_names = {a.name for a in (plan.new_abstractions if plan else [])}

    added_role_symbols = [
        s for s in diff.added_symbols if s.roles and s.props.get("kind") in ("class", "function")
    ]

    findings: list[Finding] = []

    # Check symbols not declared in plan
    for s in added_role_symbols:
        name = s.props.get("name", "")
        if name not in planned_names:
            findings.append(
                Finding(
                    id=f"abs-undeclared-{name}",
                    check=check_name,
                    severity="warn",
                    message=f"Added abstraction '{name}' with roles {s.roles} was not declared in the feature plan.",
                    path=s.props.get("path"),
                    line=s.props.get("line_start"),
                    fix_hint=f"Declare '{name}' in plan.new_abstractions with its architectural justification.",
                )
            )

    # Check count of new abstractions
    if len(added_role_symbols) > max_abstractions:
        findings.append(
            Finding(
                id="abs-count-limit",
                check=check_name,
                severity="warn",
                message=(
                    f"Diff introduces {len(added_role_symbols)} new abstractions "
                    f"(limit is {max_abstractions}). High abstraction churn increases cognitive overhead."
                ),
                fix_hint="Consider consolidating or reusing existing services before introducing new ones.",
            )
        )

    status = "warn" if findings else "pass"
    summary = (
        "All added abstractions are planned and within limits."
        if status == "pass"
        else f"{len(findings)} abstraction finding(s) detected."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
    )
