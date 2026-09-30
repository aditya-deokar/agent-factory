"""Guardrail 1: Duplication (spec §22).

For every added class/function symbol with a role: run reuse detector against the
base graph. Score >= 0.70 and not listed with a justification in
plan.new_abstractions -> fail.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...context.reuse import ProposedAbstraction, ReuseDetector
from ...workflow.feature import FeaturePlan
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ..diff import DiffFragment


def check_duplication(
    diff: DiffFragment,
    reuse_detector: ReuseDetector | None,
    plan: FeaturePlan | None = None,
) -> CheckResult:
    check_name = "duplication"
    if reuse_detector is None:
        return CheckResult(
            check=check_name,
            status="pass",
            summary="Reuse detector unavailable (no embeddings or no graph); duplication check skipped.",
        )

    planned_justifications = {a.name: a.justification for a in (plan.new_abstractions if plan else [])}

    findings: list[Finding] = []
    # Check all added role-bearing symbols
    for s in diff.added_symbols:
        props = s.props
        kind = props.get("kind", "")
        roles = s.roles
        if kind not in ("class", "function") or not roles:
            continue

        name = props.get("name", "")
        doc = props.get("doc") or ""
        methods = props.get("methods") or []
        primary_role = roles[0] if roles else None

        proposed = ProposedAbstraction(
            name=name,
            description=doc,
            methods=methods,
            role=primary_role,
        )

        try:
            report = reuse_detector.find(proposed)
        except Exception:
            continue

        top = report.top
        if top is not None and top.score >= 0.70:
            justification = planned_justifications.get(name)
            finding_id = f"dup-{name}"
            msg = (
                f"Proposed abstraction '{name}' duplicates existing '{top.name}' (similarity {top.score:.2f} >= 0.70)."
            )
            fix_hint = (
                f"Reuse or extend existing '{top.name}' "
                f"(defined in {top.path}), or declare '{name}' in the plan's new_abstractions "
                "with an explicit architectural justification."
            )
            if justification:
                # Documented in plan, do not fail
                findings.append(
                    Finding(
                        id=finding_id,
                        check=check_name,
                        severity="warn",
                        message=f"{msg} Justified in plan: {justification}",
                        path=props.get("path"),
                        line=props.get("line_start"),
                        fix_hint=fix_hint,
                        waived=True,
                        waiver_reason=f"Plan justification: {justification}",
                    )
                )
            else:
                findings.append(
                    Finding(
                        id=finding_id,
                        check=check_name,
                        severity="fail",
                        message=msg,
                        path=props.get("path"),
                        line=props.get("line_start"),
                        fix_hint=fix_hint,
                    )
                )

    has_fail = any(f.severity == "fail" and not f.waived for f in findings)
    status = "fail" if has_fail else ("warn" if findings else "pass")
    summary = (
        "No duplicate abstractions detected."
        if status == "pass"
        else f"{len(findings)} potential duplicate abstraction(s) detected."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
    )
