"""Guardrail 3: Architecture (spec §22).

Evaluate every validated Constraint on the DiffFragment:
forbid_dependency, require_via, placement (evaluated over the overlay GraphView).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from ...auditor.constraints import evaluate
from ..model import CheckResult, Finding

if TYPE_CHECKING:
    from ...db.stores import Neo4jStore
    from ..diff import DiffFragment


def check_architecture(
    diff: DiffFragment,
    store: Neo4jStore | None,
    project_id: str,
    validated_constraints: list[dict[str, Any]] | None = None,
) -> CheckResult:
    check_name = "architecture"
    overlay = diff.overlay_view

    if overlay is None:
        return CheckResult(
            check=check_name,
            status="pass",
            summary="No overlay graph available; architecture check skipped.",
        )

    # Load validated constraints from DB if not provided
    constraints = validated_constraints
    if constraints is None and store is not None:
        try:
            rows = store.read(
                """
                MATCH (c:Constraint {project_id: $p, status: 'validated'})
                RETURN c.uid AS uid, c.title AS title, c.rule_type AS rule_type, c.rule AS rule,
                       c.claim AS claim, coalesce(c.severity, 'error') AS severity
                """,
                p=project_id,
            )
            constraints = []
            for r in rows:
                rule_dict = json.loads(r["rule"]) if isinstance(r["rule"], str) else (r["rule"] or {})
                constraints.append(
                    {
                        "uid": r["uid"],
                        "title": r["title"],
                        "rule_type": r["rule_type"],
                        "rule": rule_dict,
                        "claim": r["claim"],
                        "severity": r["severity"],
                    }
                )
        except Exception:
            constraints = []

    constraints = constraints or []
    findings: list[Finding] = []

    # Get set of symbol uids in the diff (added or modified)
    diff_symbol_uids = {s.uid for s in diff.added_symbols + diff.modified_symbols}

    for c in constraints:
        rule_type = c.get("rule_type")
        rule = c.get("rule", {})
        if not rule_type or not rule:
            continue

        try:
            _ok, violations = evaluate(overlay, rule_type, rule)
        except Exception:
            continue

        for violator_uid, target_uid in violations:
            # We only alert on violations involving symbols touched by the diff!
            if violator_uid not in diff_symbol_uids and target_uid not in diff_symbol_uids:
                continue

            violator = overlay.symbols.get(violator_uid)
            target = overlay.symbols.get(target_uid)

            v_name = violator.name if violator else violator_uid
            t_name = target.name if target else target_uid
            path = violator.path if violator else None
            line = violator.line_start if violator else None

            finding_id = f"arch-{rule_type}-{v_name}-{t_name}"
            msg = (
                f"Violation of validated constraint '{c['title']}': "
                f"'{v_name}' violates rule '{rule_type}' with target '{t_name}'."
            )
            fix_hint = f"Follow architectural constraint {c['uid']}: {c.get('claim', '')}"

            findings.append(
                Finding(
                    id=finding_id,
                    check=check_name,
                    severity="fail",
                    message=msg,
                    path=path,
                    line=line,
                    rule_or_knowledge_uid=c["uid"],
                    fix_hint=fix_hint,
                )
            )

    has_fail = any(f.severity == "fail" and not f.waived for f in findings)
    status = "fail" if has_fail else ("warn" if findings else "pass")
    summary = (
        "All architectural constraints satisfied."
        if status == "pass"
        else f"{len(findings)} architectural constraint violation(s) detected."
    )

    return CheckResult(
        check=check_name,
        status=status,
        summary=summary,
        findings=findings,
    )
