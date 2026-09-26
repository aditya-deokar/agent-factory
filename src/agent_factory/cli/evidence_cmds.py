"""`agent-factory evidence ...`: evidence collection, verification, and inspection (spec §23)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ..evidence.collectors.diff import collect_diff_evidence
from ..evidence.collectors.runner import collect_command_evidence
from ..evidence.store import EvidenceStore
from ..guardrails.diff import analyze_diff
from ..guardrails.rules import run_guardrails
from .common import emit, fail
from .context_cmds import runtime

evidence_app = typer.Typer(help="Evidence store: collect, add, list, and verify artifacts.", no_args_is_help=True)


@evidence_app.command("collect")
def collect(
    ctx: typer.Context,
    feature_id: Annotated[str | None, typer.Option("--feature", help="Feature ID (defaults to active)")] = None,
) -> None:
    """Collect evidence automatically: diff stat/patch, checks output, and guardrail report."""
    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        state = service.active(feature_id)
        if state is None:
            fail("no active feature on this branch; run: agent-factory feature start <name>")

        store = EvidenceStore(rt.root, state.feature_id)

        # 1. Collect diff
        collect_diff_evidence(rt.root, store, base_sha=state.base_sha)

        # 2. Collect test/build/lint command runs
        collect_command_evidence(rt.root, store, checks_config=rt.config.checks)

        # 3. Collect guardrail report
        diff = analyze_diff(rt.root, base_sha=state.base_sha, project_id=rt.project_id)
        report = run_guardrails(
            diff,
            rt,
            plan=state.plan,
            waivers=state.waivers,
            feature_id=state.feature_id,
        )
        store.record_text(
            kind="guardrail",
            rel_dest="guardrail-report.md",
            content=report.to_markdown(),
            summary=f"Guardrail evaluation report ({report.status.upper()})",
        )

        items = store.list_items()
        verified, errors = store.verify()

    def render(c: Console) -> None:
        status_text = "[green]✓ Verified[/]" if verified else "[red]✗ Tampered/Missing[/]"
        c.print(f"Collected [bold]{len(items)}[/] evidence artifacts for {state.feature_id} ({status_text})")
        for it in items:
            c.print(f"  - [{it.kind}] {it.path} ({it.summary})")

    emit(ctx, {"feature_id": state.feature_id, "verified": verified, "items": [i.model_dump() for i in items]}, render)
    if not verified:
        sys.exit(1)


@evidence_app.command("add")
def add(
    ctx: typer.Context,
    kind: Annotated[str, typer.Argument(help="Artifact kind: test | build | lint | screenshot | recording | other")],
    path: Annotated[Path, typer.Argument(help="Path to the artifact file")],
    summary: Annotated[str, typer.Option("--summary", "-s", help="Description of the evidence")],
    feature_id: Annotated[str | None, typer.Option("--feature")] = None,
) -> None:
    """Register an existing artifact file into the active feature's evidence store."""
    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        state = service.active(feature_id)
        if state is None:
            fail("no active feature on this branch; run: agent-factory feature start <name>")

        file_path = path if path.is_absolute() else (rt.root / path)
        if not file_path.exists():
            fail(f"artifact file not found: {path}")

        store = EvidenceStore(rt.root, state.feature_id)
        item = store.add_artifact(kind, file_path, summary)

    emit(ctx, item, lambda c: c.print(f"Registered evidence [bold]{item.id}[/]: {item.path} (SHA {item.sha256[:10]}...)"))


@evidence_app.command("list")
def list_evidence(
    ctx: typer.Context,
    feature_id: Annotated[str | None, typer.Option("--feature")] = None,
) -> None:
    """List all evidence artifacts registered for the active feature."""
    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        state = service.active(feature_id)
        if state is None:
            fail("no active feature on this branch")

        store = EvidenceStore(rt.root, state.feature_id)
        items = store.list_items()
        verified, errors = store.verify()

    def render(c: Console) -> None:
        if not items:
            c.print(f"No evidence artifacts registered yet for {state.feature_id}.")
            return
        t = Table(title=f"Evidence for {state.feature_id}")
        t.add_column("ID")
        t.add_column("Kind")
        t.add_column("Path")
        t.add_column("SHA-256")
        t.add_column("Summary")
        for it in items:
            t.add_row(it.id, it.kind, it.path, it.sha256[:12] + "...", it.summary)
        c.print(t)
        if not verified:
            c.print(f"[red]Warning: evidence integrity check failed: {', '.join(errors)}[/]")

    emit(ctx, items, render)


@evidence_app.command("verify")
def verify(
    ctx: typer.Context,
    feature_id: Annotated[str | None, typer.Option("--feature")] = None,
) -> None:
    """Verify that all evidence artifacts exist on disk and match their recorded SHA-256 hashes."""
    with runtime(ctx, need_audit=False) as rt:
        service = rt.features()
        state = service.active(feature_id)
        if state is None:
            fail("no active feature on this branch")

        store = EvidenceStore(rt.root, state.feature_id)
        ok, errors = store.verify()

    def render(c: Console) -> None:
        if ok:
            c.print(f"[green]✓ All evidence artifacts verified for {state.feature_id}.[/]")
        else:
            c.print(f"[red]✗ Evidence verification failed for {state.feature_id}:[/]")
            for err in errors:
                c.print(f"  - {err}")

    emit(ctx, {"verified": ok, "errors": errors}, render)
    if not ok:
        sys.exit(1)
