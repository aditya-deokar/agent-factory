"""`agent-factory pr-body`: render an evidence-backed pull request body (spec §24)."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from ..evidence.pr_body import render_pr_body
from ..evidence.store import EvidenceStore
from ..guardrails.diff import analyze_diff
from ..guardrails.model import GuardrailReport
from .common import emit, fail
from .context_cmds import runtime

pr_body_app = typer.Typer(help="Render an evidence-backed PR body from data only.", no_args_is_help=False)


@pr_body_app.callback(invoke_without_command=True)
def pr_body(
    ctx: typer.Context,
    feature_id: Annotated[str | None, typer.Option("--feature", help="Feature ID (defaults to active)")] = None,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Write PR markdown to file")] = None,
) -> None:
    """Render the evidence-backed pull request description from data only."""
    if ctx.invoked_subcommand is not None:
        return

    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        state = service.active(feature_id)
        if state is None:
            fail("no active feature on this branch; start one with agent-factory feature start <name>")

        ev_store = EvidenceStore(rt.root, state.feature_id)
        diff = analyze_diff(rt.root, base_sha=state.base_sha, project_id=rt.project_id)

        # Load guardrail report if available
        guardrail_report = None
        gr_path = ev_store.dir / "guardrail-report.json"
        if gr_path.exists():
            with contextlib.suppress(Exception):
                guardrail_report = GuardrailReport.model_validate_json(gr_path.read_text(encoding="utf-8"))

        body = render_pr_body(state, diff, ev_store, guardrail_report=guardrail_report)

        if out:
            out_path = out if out.is_absolute() else (rt.root / out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(body, encoding="utf-8")

    def render(c: Console) -> None:
        if out:
            c.print(f"PR body written to [bold]{out}[/]")
        else:
            c.print(body, markup=False)

    emit(ctx, {"feature_id": state.feature_id, "pr_body": body}, render)
