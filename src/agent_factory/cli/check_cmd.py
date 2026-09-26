"""`agent-factory check`: run the 8 anti-slop guardrails (spec §22)."""

from __future__ import annotations

import sys
from typing import Annotated

import typer
from rich.console import Console

from ..guardrails.diff import analyze_diff
from ..guardrails.rules import ALL_CHECKS, run_guardrails
from .common import emit, fail
from .context_cmds import runtime

check_app = typer.Typer(help="Run anti-slop guardrails on the active feature or working tree.", no_args_is_help=False)


@check_app.callback(invoke_without_command=True)
def check(
    ctx: typer.Context,
    feature_id: Annotated[str | None, typer.Option("--feature", help="Feature ID to check (defaults to active)")] = None,
    only: Annotated[str | None, typer.Option("--only", help="Comma-separated check names (e.g. duplication,architecture)")] = None,
    waive: Annotated[str | None, typer.Option("--waive", help="Finding ID to waive")] = None,
    reason: Annotated[str | None, typer.Option("--reason", help="Justification reason for the waiver")] = None,
    base: Annotated[str | None, typer.Option("--base", help="Base commit SHA to diff against")] = None,
) -> None:
    """Run anti-slop guardrails on code changes against the repository's knowledge graph."""
    if ctx.invoked_subcommand is not None:
        return

    only_list = [c.strip() for c in only.split(",") if c.strip()] if only else None
    if only_list:
        for c in only_list:
            if c not in ALL_CHECKS:
                fail(f"unknown check '{c}'; valid checks: {', '.join(ALL_CHECKS)}")

    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        state = service.active(feature_id)

        # Handle waiver registration if requested
        if waive:
            if not reason:
                fail("a --reason is required when waiving a finding")
            if state is None:
                fail("cannot waive finding: no active feature session on this branch")
            state = service.waive(feature_id, waive, reason)

        base_sha = base or (state.base_sha if state else None)
        diff = analyze_diff(rt.root, base_sha=base_sha, project_id=rt.project_id)

        report = run_guardrails(
            diff,
            rt,
            plan=state.plan if state else None,
            waivers=state.waivers if state else None,
            only=only_list,
            test_baseline=state.test_baseline if state else None,
            feature_id=state.feature_id if state else None,
        )

        if state is not None:
            state.guardrails = report.model_dump(mode="json")
            service.save(state)

    def render(c: Console) -> None:
        c.print(report.to_markdown())

    emit(ctx, report, render)
    if report.status == "fail":
        sys.exit(1)
