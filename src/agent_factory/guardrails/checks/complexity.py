"""Guardrail 6: Complexity & Dependencies (spec §22).

Checks:
- New external dependencies.
- A dependency in a category already served (forbid_external_dep, e.g. a 2nd state-management or queue lib) -> fail if violates constraint, else warn.
- New infra files (Dockerfile, docker-compose.yml, CI pipelines) -> warn.
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any

from ...auditor.constraints import DEP_CATEGORIES
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ..diff import DiffFragment

_INFRA_FILES = {
    "dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "procfile",
    "k8s",
    "helm",
    "terraform",
    ".github",
    ".gitlab-ci.yml",
}


def _category_of(dep: str) -> str | None:
    d = dep.lower()
    for cat, pkgs in DEP_CATEGORIES.items():
        if d in pkgs:
            return cat
    return None


def check_complexity(
    diff: DiffFragment,
    existing_deps: dict[str, str] | None = None,
    validated_constraints: list[dict[str, Any]] | None = None,
) -> CheckResult:
    check_name = "complexity"
    findings: list[Finding] = []

    # Map existing deps to categories
    existing = existing_deps or (diff.overlay_view.external_deps if diff.overlay_view else {})
    existing_by_cat: dict[str, list[str]] = {}
    for dep in existing:
        cat = _category_of(dep)
        if cat:
            existing_by_cat.setdefault(cat, []).append(dep)

    # Check forbidden external deps from constraints
    forbidden_deps: dict[str, str] = {}
    for c in validated_constraints or []:
        if c.get("rule_type") == "forbid_external_dep":
            rule = c.get("rule", {})
            for pkg in rule.get("forbidden_packages", []):
                forbidden_deps[pkg] = c.get("title", "forbidden external dependency")

    # 1. New external dependencies
    for dep, manifest in diff.new_external_deps.items():
        cat = _category_of(dep)
        finding_id = f"complex-dep-{dep}"

        # If forbidden by constraint:
        if dep in forbidden_deps:
            findings.append(
                Finding(
                    id=finding_id,
                    check=check_name,
                    severity="fail",
                    message=f"New dependency '{dep}' violates validated constraint: {forbidden_deps[dep]}.",
                    path=manifest,
                    fix_hint=f"Remove '{dep}' and use the approved alternative in the repository.",
                )
            )
            continue

        # If in a category already served:
        if cat and cat in existing_by_cat and dep not in existing_by_cat[cat]:
            curr = ", ".join(existing_by_cat[cat])
            findings.append(
                Finding(
                    id=finding_id,
                    check=check_name,
                    severity="fail",
                    message=(
                        f"Introduced duplicate dependency category '{cat}': '{dep}' was added, "
                        f"but '{curr}' already exists in the repository."
                    ),
                    path=manifest,
                    fix_hint=f"Consolidate on existing '{curr}' rather than introducing '{dep}'.",
                )
            )
        else:
            findings.append(
                Finding(
                    id=finding_id,
                    check=check_name,
                    severity="warn",
                    message=f"New external dependency '{dep}' added in '{manifest}'.",
                    path=manifest,
                    fix_hint="Verify this dependency is strictly necessary and meets security policies.",
                )
            )

    # 2. New infrastructure files
    for f in diff.files:
        if f.status == "A":
            p_lower = f.path.lower()
            base_name = posixpath.basename(p_lower)
            if base_name in _INFRA_FILES or any(part in _INFRA_FILES for part in p_lower.split("/")):
                findings.append(
                    Finding(
                        id=f"complex-infra-{base_name}",
                        check=check_name,
                        severity="warn",
                        message=f"New infrastructure file '{f.path}' introduced.",
                        path=f.path,
                        fix_hint="Ensure infrastructure additions comply with deployment standards.",
                    )
                )

    has_fail = any(f.severity == "fail" and not f.waived for f in findings)
    status = "fail" if has_fail else ("warn" if findings else "pass")
    summary = (
        "No complexity or dependency issues detected."
        if status == "pass"
        else f"{len(findings)} complexity/dependency finding(s)."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
    )
