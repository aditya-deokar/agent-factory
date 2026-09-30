"""Rule engine: runs the 8 guardrail checks, applies waivers, and generates reports."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from ..workflow.feature import FeaturePlan
from .checks import (
    check_abstraction,
    check_architecture,
    check_complexity,
    check_consistency,
    check_duplication,
    check_regression,
    check_reusability,
    check_scope,
)
from .diff import DiffFragment
from .model import CheckResult, GuardrailReport

if TYPE_CHECKING:
    from ..runtime import Runtime

ALL_CHECKS = (
    "duplication",
    "abstraction",
    "architecture",
    "reusability",
    "consistency",
    "complexity",
    "scope",
    "regression",
)


def run_guardrails(
    diff: DiffFragment,
    rt: Runtime,
    plan: FeaturePlan | None = None,
    waivers: dict[str, str] | None = None,
    only: list[str] | None = None,
    test_baseline: int | None = None,
    runner: Callable[[str, Path], tuple[int, str]] | None = None,
    feature_id: str | None = None,
) -> GuardrailReport:
    """Run all or selected guardrails over the diff fragment."""
    selected = set(only) if only else set(ALL_CHECKS)
    waiver_map = waivers or {}
    results: list[CheckResult] = []

    # 1. Duplication
    if "duplication" in selected:
        res = check_duplication(diff, rt.reuse() if hasattr(rt, "reuse") else None, plan)
        results.append(res)

    # 2. Abstraction
    if "abstraction" in selected:
        results.append(check_abstraction(diff, plan))

    # 3. Architecture
    if "architecture" in selected:
        results.append(
            check_architecture(
                diff,
                rt.store if hasattr(rt, "store") else None,
                rt.project_id,
            )
        )

    # 4. Reusability
    if "reusability" in selected:
        results.append(check_reusability(diff))

    # 5. Consistency
    if "consistency" in selected:
        results.append(check_consistency(diff))

    # 6. Complexity
    if "complexity" in selected:
        results.append(check_complexity(diff))

    # 7. Scope
    if "scope" in selected:
        results.append(check_scope(diff, plan))

    # 8. Regression
    if "regression" in selected:
        cfg = getattr(rt.config, "checks", None)
        results.append(
            check_regression(
                rt.root,
                checks_config=cfg,
                test_baseline=test_baseline,
                runner=runner,
            )
        )

    # Apply waivers to findings
    for c in results:
        for f in c.findings:
            if f.id in waiver_map:
                f.waived = True
                f.waiver_reason = waiver_map[f.id]

    report = GuardrailReport.from_checks(rt.project_id, results, feature_id=feature_id)

    # Write report if feature_id is active
    if feature_id and rt.root:
        out_dir = rt.root / ".agent-factory" / "evidence" / feature_id
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "guardrail-report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
            (out_dir / "guardrail-report.md").write_text(report.to_markdown(), encoding="utf-8")
        except OSError:
            pass

    return report
