"""`agent-factory feature ...`: feature sessions from the CLI (the MCP tools' twins, for agents without MCP)."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from .common import EXIT_CONFIG, emit, fail
from .context_cmds import runtime

feature_app = typer.Typer(help="Feature sessions: start, plan, record steps, inspect.", no_args_is_help=True)


@feature_app.command("start")
def start(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Short feature name")],
    request: Annotated[str, typer.Option(help="The full request (defaults to the name)")] = "",
    branch: Annotated[str | None, typer.Option(help="Branch to map the session to (default: current)")] = None,
) -> None:
    """Start a feature session on the current branch (records the request and opens a reasoning trace)."""
    with runtime(ctx) as rt:
        state = asyncio.run(rt.features().start(name, request or name, branch))
    emit(ctx, state, lambda c: c.print(f"Started [bold]{state.feature_id}[/] on branch {state.branch or '-'}"))


@feature_app.command("plan")
def plan(
    ctx: typer.Context,
    file: Annotated[Path | None, typer.Option(help="Plan JSON file (default: read stdin)")] = None,
    feature_id: Annotated[str | None, typer.Option("--feature")] = None,
) -> None:
    """Store the plan (JSON matching FeaturePlan: summary, reuse_decisions, planned_files, new_abstractions...)."""
    from pydantic import ValidationError

    from ..workflow.feature import FeatureNotFound, FeaturePlan

    raw = file.read_text(encoding="utf-8") if file else sys.stdin.read()
    try:
        parsed = FeaturePlan.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as error:
        fail(f"invalid plan: {error}", EXIT_CONFIG)
    with runtime(ctx) as rt:
        try:
            state = asyncio.run(rt.features().record_plan(feature_id, parsed))
        except FeatureNotFound as error:
            fail(str(error))
    emit(ctx, state, lambda c: c.print(f"Plan stored for {state.feature_id} (status {state.status})"))


@feature_app.command("step")
def step(
    ctx: typer.Context,
    thought: Annotated[str, typer.Argument(help="What you did or learned, e.g. 'tried X, failed because Y'")],
    feature_id: Annotated[str | None, typer.Option("--feature")] = None,
) -> None:
    """Record a reasoning step in the active feature's trace."""
    with runtime(ctx, need_audit=False) as rt:
        state = asyncio.run(rt.features().record_step(feature_id, thought, action="note"))
    if state is None:
        fail("no active feature on this branch; run: agent-factory feature start <name>")
    emit(ctx, state, lambda c: c.print(f"Step {state.steps} recorded for {state.feature_id}"))


@feature_app.command("status")
def status(ctx: typer.Context, feature_id: Annotated[str | None, typer.Argument()] = None) -> None:
    """The active feature (or the one given) with its plan; or all sessions when none is active."""
    from ..workflow.feature import FeatureNotFound, render_plan

    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        try:
            state = service.active(feature_id)
        except FeatureNotFound as error:
            fail(str(error))
        sessions = service.list()
    if state is not None:
        emit(ctx, state, lambda c: c.print(render_plan(state), markup=False))
        return
    def render(c: Console) -> None:
        if not sessions:
            c.print("No feature sessions yet.")
        for s in sessions:
            c.print(f"{s.feature_id:<45} {s.status:<12} {s.branch or '-'}", markup=False)

    emit(ctx, sessions, render)
