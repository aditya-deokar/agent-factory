"""`agent-factory audit`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ..auditor.pipeline import AuditOptions, run_audit
from .common import emit, state


def audit(
    ctx: typer.Context,
    full: Annotated[bool, typer.Option("--full", help="Re-write everything instead of only changed files")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Extract and detect, but write nothing")] = False,
    no_embed: Annotated[bool, typer.Option("--no-embed", help="Skip embeddings (offline audit)")] = False,
    no_history: Annotated[bool, typer.Option("--no-history", help="Skip git history mining")] = False,
    dump: Annotated[
        Path | None, typer.Option(help="With --dry-run: write the normalized graph fragment as JSON")
    ] = None,
) -> None:
    """Build or refresh project memory from the repository. Never modifies your code."""
    st = state(ctx)
    config = st.config()
    opts = AuditOptions(full=full, dry_run=dry_run, embed=not no_embed, history=not no_history)
    if dry_run:
        report = run_audit(st.root, config, None, opts)
        if dump and report.fragment is not None:
            dump.write_text(json.dumps(report.fragment.normalized(), indent=1, default=list), encoding="utf-8")
    else:
        with st.stores(config) as stores:
            report = run_audit(st.root, config, stores, opts)

    def render(c: Console) -> None:
        c.print(
            f"[bold]Audit[/] {report.mode} · {report.project_id} @ {str(report.commit)[:7]} · {report.duration_s:.1f}s"
        )
        files = report.files
        c.print(
            f"Files: {files.get('parsed', 0)} parsed · {files.get('changed', files.get('parsed', 0))} changed · "
            f"skipped {files.get('skipped', {})}"
        )
        t = Table(title="Architecture found", show_header=True)
        t.add_column("role")
        t.add_column("count", justify="right")
        for role, n in report.roles.items():
            t.add_row(role, str(n))
        c.print(t)
        c.print("Edges: " + " · ".join(f"{k} {v}" for k, v in report.edges.items()))
        if report.docs:
            c.print(f"Docs: {report.docs['docs']} ({report.docs['adrs']} ADRs, {report.docs['chunks']} chunks)")
        if report.history:
            c.print(
                f"History: {report.history['new_commits']} new commits · "
                f"{report.history['co_change_pairs']} co-change pairs"
            )
        if report.knowledge:
            c.print(f"Knowledge: {report.knowledge['by_status']}")
        if report.revalidation.get("deprecated") or report.revalidation.get("revalidated"):
            c.print(
                f"Revalidation: {len(report.revalidation['deprecated'])} deprecated · "
                f"{len(report.revalidation['revalidated'])} re-validated"
            )
        if report.candidates:
            ct = Table(title=f"{len(report.candidates)} candidates to review")
            for col in ("kind", "title", "confidence", "support", "violations"):
                ct.add_column(col)
            for cand in report.candidates[:15]:
                ct.add_row(
                    cand["kind"],
                    cand["title"],
                    str(cand.get("confidence", "")),
                    str(cand["support"]),
                    str(cand["violations"]),
                )
            c.print(ct)
        for w in report.warnings:
            c.print(f"[yellow]warning:[/] {w}")
        if not dry_run:
            c.print(
                "Next: review candidates with [bold]agent-factory memory review[/] "
                "or ask your agent to run the project-audit skill."
            )

    emit(ctx, report, render)
