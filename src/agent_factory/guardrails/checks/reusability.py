"""Guardrail 4: Reusability & Layering (spec §22).

Business logic in the wrong layer:
- A controller or route method with > 25 LOC -> warn.
- Direct call from controller/route to repository/model/SDK -> warn.
- A new service that is not exported -> warn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...schema.model import Role
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ..diff import DiffFragment

MAX_CONTROLLER_METHOD_LOC = 25


def check_reusability(diff: DiffFragment) -> CheckResult:
    check_name = "reusability"
    findings: list[Finding] = []
    overlay = diff.overlay_view

    for s in diff.added_symbols + diff.modified_symbols:
        props = s.props
        roles = s.roles
        name = props.get("name", "")
        kind = props.get("kind", "")
        loc = props.get("loc", 0)
        path = props.get("path")
        line = props.get("line_start")

        # 1. Controller/Route LOC check
        if Role.CONTROLLER.value in roles or Role.ROUTE.value in roles:
            if kind in ("function", "method") and loc > MAX_CONTROLLER_METHOD_LOC:
                findings.append(
                    Finding(
                        id=f"reuse-controller-loc-{name}",
                        check=check_name,
                        severity="warn",
                        message=(
                            f"Controller/route '{name}' has {loc} lines of code "
                            f"(threshold is {MAX_CONTROLLER_METHOD_LOC}). "
                            "Controllers should only orchestrate; extract business logic into a Service."
                        ),
                        path=path,
                        line=line,
                        fix_hint="Extract multi-step operations and domain rules into a domain service.",
                    )
                )

        # 2. Service export check
        if Role.SERVICE.value in roles and kind == "class":
            if not props.get("exported", False):
                findings.append(
                    Finding(
                        id=f"reuse-unexported-service-{name}",
                        check=check_name,
                        severity="warn",
                        message=f"Service '{name}' is defined but not exported for reuse.",
                        path=path,
                        line=line,
                        fix_hint="Export the service class so other modules and controllers can reuse it.",
                    )
                )

    # 3. Direct controller to repository/model check
    if overlay:
        for s in diff.added_symbols + diff.modified_symbols:
            if Role.CONTROLLER.value in s.roles:
                deps = overlay.deps(s.uid, ("USES", "CALLS", "ACCESSES"))
                for d in deps:
                    d_sym = overlay.symbols.get(d)
                    if d_sym and (Role.REPOSITORY.value in d_sym.roles or Role.MODEL.value in d_sym.roles):
                        findings.append(
                            Finding(
                                id=f"reuse-layering-{s.props.get('name')}-{d_sym.name}",
                                check=check_name,
                                severity="warn",
                                message=(
                                    f"Controller '{s.props.get('name')}' directly accesses "
                                    f"data layer '{d_sym.name}'. Controllers should call Services."
                                ),
                                path=s.props.get("path"),
                                line=s.props.get("line_start"),
                                fix_hint=f"Delegate data access to an intermediate Service rather than calling '{d_sym.name}' directly.",
                            )
                        )

    status = "warn" if findings else "pass"
    summary = (
        "Layering and reusability clean."
        if status == "pass"
        else f"{len(findings)} layering/reusability suggestion(s)."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
    )
