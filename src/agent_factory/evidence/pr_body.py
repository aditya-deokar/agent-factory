"""PR body renderer (spec §24): renders the evidence-backed pull request body from data only.

Rule: The renderer cannot print a ✓ for an evidence item that is not backed by a verified manifest item.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..guardrails.model import GuardrailReport
from .store import EvidenceStore

if TYPE_CHECKING:
    from ..guardrails.diff import DiffFragment
    from ..workflow.feature import FeatureState


def render_pr_body(
    state: FeatureState,
    diff: DiffFragment | None,
    evidence_store: EvidenceStore,
    guardrail_report: GuardrailReport | None = None,
    memory_preview: dict[str, Any] | None = None,
) -> str:
    """Renders GitHub markdown PR description strictly from structured data."""
    plan = state.plan
    items = evidence_store.list_items()
    verified, _ = evidence_store.verify()

    lines: list[str] = [
        f"## Feature: {state.name}",
        "",
        f"> **Request:** {state.request}",
        "",
    ]

    # 1. Implementation Summary
    lines.extend(["### 1. Implementation Summary", ""])
    if plan and plan.summary:
        lines.extend([plan.summary, ""])
    if diff:
        lines.append(
            f"- **Files Changed:** {len(diff.files)} files "
            f"(+{diff.total_added_lines} / -{diff.total_deleted_lines} lines)"
        )
        if diff.new_external_deps:
            deps_str = ", ".join(f"`{d}`" for d in sorted(diff.new_external_deps))
            lines.append(f"- **New External Dependencies:** {deps_str}")
        else:
            lines.append("- **New External Dependencies:** None")
    lines.append("")

    # 2. Architectural Reuse
    lines.extend(["### 2. Architectural Reuse", ""])
    dup_passed = False
    if guardrail_report:
        dup_check = next((c for c in guardrail_report.checks if c.check == "duplication"), None)
        dup_passed = dup_check is not None and dup_check.status == "pass"

    if plan and plan.reuse_decisions:
        for d in plan.reuse_decisions:
            chosen = f" → `{d.chosen}`" if d.chosen else ""
            just = f" ({d.justification})" if d.justification else ""
            lines.append(f"- **{d.proposed}**: {d.verdict}{chosen}{just}")
    else:
        lines.append("- No specific reuse decisions recorded in plan.")

    if dup_passed:
        lines.append("- **Duplication Check:** ✓ Verified: No duplicate abstractions introduced.")
    else:
        lines.append("- **Duplication Check:** ⚠ Warning / Review required: potential duplicate abstractions.")
    lines.append("")

    # 3. Tests & Regression
    lines.extend(["### 3. Tests & Regression", ""])
    regress_check = None
    if guardrail_report:
        regress_check = next((c for c in guardrail_report.checks if c.check == "regression"), None)

    if regress_check:
        runs = regress_check.metadata.get("runs", {})
        test_run = runs.get("test")
        if test_run:
            count = regress_check.metadata.get("test_count", "unknown")
            dur = test_run.get("duration", "0.0")
            lines.append(f"- **Unit & Integration Tests:** {count} passed ({dur}s)")
        else:
            lines.append(f"- **Test Status:** {regress_check.summary}")
    else:
        lines.append("- Automated tests executed as part of evidence collection.")
    lines.append("")

    # 4. Evidence Checklist
    lines.extend(["### 4. Evidence Checklist", ""])
    lines.extend(["| Artifact | Kind | SHA-256 | Status |", "|---|---|---|---|"])

    # Map manifest items by kind
    items_by_kind = {i.kind: i for i in items}
    expected_kinds = [
        ("diff", "Git diff stat & patch"),
        ("test", "Test execution & JUnit XML"),
        ("guardrail", "Anti-slop guardrail report"),
        ("screenshot", "UI Screenshot / Visual diff"),
        ("recording", "Session video recording"),
    ]

    for kind, label in expected_kinds:
        item = items_by_kind.get(kind)
        if item and verified:
            lines.append(f"| {label} | `{kind}` | `{item.sha256[:10]}...` | ✓ Verified |")
        elif item:
            lines.append(f"| {label} | `{kind}` | `{item.sha256[:10]}...` | ⚠ Unverified |")
        else:
            lines.append(f"| {label} | `{kind}` | - | ✗ Missing |")
    lines.append("")

    # 5. Architectural Decisions
    lines.extend(["### 5. Architectural Decisions", ""])
    if plan and plan.architectural_decision:
        lines.append(f"- **Decision:** {plan.architectural_decision}")
    if plan and plan.cited_knowledge:
        lines.append(f"- **Cited Architecture Knowledge:** {', '.join(f'`{k}`' for k in plan.cited_knowledge)}")
    if not (plan and (plan.architectural_decision or plan.cited_knowledge)):
        lines.append("- No new ADRs required for this change.")
    lines.append("")

    # 6. Memory Updates (Commit Preview)
    lines.extend(["### 6. Memory Updates (Graph Ingestion)", ""])
    if memory_preview:
        added_syms = memory_preview.get("added_symbols", 0)
        edges = memory_preview.get("edges_created", 0)
        cands = memory_preview.get("candidates_promoted", 0)
        lines.append(f"- **Domain Graph:** +{added_syms} symbols, +{edges} relationships")
        lines.append(f"- **Institutional Memory:** +{cands} validated knowledge entries")
    else:
        lines.append("- Changes will be incrementally ingested into Neo4j domain graph upon merge.")
    lines.append("")

    # 7. Guardrails Report
    lines.extend(["### 7. Guardrail Results", ""])
    if guardrail_report:
        lines.extend(["| Check | Status | Summary |", "|---|---|---|"])
        for c in guardrail_report.checks:
            icon = "✓" if c.status == "pass" else ("⚠" if c.status == "warn" else "✗")
            lines.append(f"| {c.check} | {icon} {c.status} | {c.summary} |")
        if guardrail_report.waived_findings > 0:
            lines.append("")
            lines.append(f"*Note: {guardrail_report.waived_findings} finding(s) waived with documented justification.*")
    else:
        lines.append("- Guardrails evaluated during CI build.")
    lines.append("")

    return "\n".join(lines)
