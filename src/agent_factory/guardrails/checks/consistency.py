"""Guardrail 5: Consistency (spec §22).

New symbols vs validated naming, placement, and validation patterns
(e.g. a new route without validation schema pattern).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...schema.model import Role
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ..diff import DiffFragment


def check_consistency(diff: DiffFragment) -> CheckResult:
    check_name = "consistency"
    findings: list[Finding] = []

    for s in diff.added_symbols:
        props = s.props
        roles = s.roles
        name = props.get("name", "")
        kind = props.get("kind", "")
        path = props.get("path", "")
        line = props.get("line_start")

        # 1. Route validation pattern
        if Role.ROUTE.value in roles or kind == "route":
            validated = props.get("validated") or props.get("validator")
            if not validated:
                findings.append(
                    Finding(
                        id=f"const-route-unvalidated-{name}",
                        check=check_name,
                        severity="warn",
                        message=f"Route '{name}' lacks an associated input validation schema.",
                        path=path,
                        line=line,
                        fix_hint="Attach a validation schema (e.g. Zod or Joi validator) to the route handler.",
                    )
                )

        # 2. Service naming pattern
        if Role.SERVICE.value in roles and kind == "class":
            if not name.endswith("Service"):
                findings.append(
                    Finding(
                        id=f"const-service-naming-{name}",
                        check=check_name,
                        severity="warn",
                        message=f"Service class '{name}' does not follow the '...Service' naming convention.",
                        path=path,
                        line=line,
                        fix_hint=f"Rename '{name}' to '{name}Service' for project consistency.",
                    )
                )

        # 3. Repository naming pattern
        if Role.REPOSITORY.value in roles and kind == "class":
            if not (name.endswith("Repository") or name.endswith("Repo")):
                findings.append(
                    Finding(
                        id=f"const-repo-naming-{name}",
                        check=check_name,
                        severity="warn",
                        message=f"Repository class '{name}' does not follow the '...Repository' naming convention.",
                        path=path,
                        line=line,
                        fix_hint=f"Rename '{name}' to '{name}Repository'.",
                    )
                )

    status = "warn" if findings else "pass"
    summary = (
        "Consistency patterns satisfied."
        if status == "pass"
        else f"{len(findings)} consistency finding(s)."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
    )
