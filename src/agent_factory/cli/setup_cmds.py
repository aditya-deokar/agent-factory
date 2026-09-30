"""`init`, `doctor`, `status`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from ..common.paths import NotAGitRepo, find_git_root, head_commit
from ..setup.checks import run_doctor
from ..setup.init_project import InitOptions, run_init
from .common import EXIT_CONFIG, STATUS_STYLE, emit, fail, state


def init(
    ctx: typer.Context,
    agents: Annotated[str, typer.Option(help="Comma-separated: claude-code,cursor,vscode,codex")] = (
        "claude-code,cursor,vscode"
    ),
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show planned changes without writing")] = False,
    skills: Annotated[bool, typer.Option("--skills/--no-skills", help="Install skills with npx skills add")] = False,
    aura_instance: Annotated[str | None, typer.Option(help="Also add the hosted Aura MCP for this instance id")] = None,
    graphacademy: Annotated[bool, typer.Option(help="Also add the GraphAcademy MCP server")] = False,
    project_id: Annotated[str | None, typer.Option(help="Project id (default: repo folder name)")] = None,
) -> None:
    """Set up Agent Factory in this repository (idempotent; never deletes anything)."""
    st = state(ctx)
    try:
        root = find_git_root(st.repo_opt or Path.cwd())
    except NotAGitRepo as error:
        fail(f"{error}. Agent Factory works inside a git repository.", EXIT_CONFIG)
    opts = InitOptions(
        agents=[a.strip() for a in agents.split(",") if a.strip()],
        dry_run=dry_run,
        install_skills=skills,
        aura_instance=aura_instance,
        graphacademy=graphacademy,
        project_id=project_id,
    )
    try:
        result = run_init(root, opts)
    except ValueError as error:
        fail(str(error), EXIT_CONFIG)

    def render(c: Console) -> None:
        title = "agent-factory init" + (" (dry run)" if dry_run else "")
        table = Table(title=f"{title}: {result.repo}", show_lines=False)
        table.add_column("path")
        table.add_column("action")
        table.add_column("detail", overflow="fold")
        for a in result.actions:
            table.add_row(a.path, f"[{STATUS_STYLE[a.kind]}]{a.kind}[/]", a.detail)
        c.print(table)
        for note in result.notes:
            c.print(f"[yellow]note:[/] {note}")
        c.print("Next: " + "  →  ".join(f"[bold]{s}[/]" for s in result.next_steps))

    emit(ctx, result, render)


def doctor(
    ctx: typer.Context,
    online: Annotated[bool, typer.Option("--online", help="Also call the embeddings endpoint")] = False,
) -> None:
    """Check config, env, Neo4j, schema, MCP configs and skills."""
    st = state(ctx)
    report = run_doctor(st.root, online=online)

    def render(c: Console) -> None:
        table = Table(title="agent-factory doctor", caption=report.repo)
        table.add_column("check")
        table.add_column("status")
        table.add_column("detail", overflow="fold")
        table.add_column("fix", overflow="fold")
        for chk in report.checks:
            table.add_row(chk.name, f"[{STATUS_STYLE[chk.status]}]{chk.status}[/]", chk.detail, chk.hint)
        c.print(table)
        s = report.summary()
        c.print(f"[green]{s['pass']} pass[/] · [yellow]{s['warn']} warn[/] · [red]{s['fail']} fail[/]")

    emit(ctx, report, render)
    raise typer.Exit(report.exit_code)


def status(ctx: typer.Context) -> None:
    """Harness state: config, database, last audit, memory counts."""
    st = state(ctx)
    config = st.config()
    result: dict[str, Any] = {"project": config.project.id, "repo": str(st.root), "head": head_commit(st.root)}
    with st.stores(config) as stores:
        from ..db.migrations import current_version
        from ..db.repos import AuditRunRepo, KnowledgeRepo

        result["schema_version"] = current_version(stores.domain)
        result["same_instance"] = stores.same_instance
        result["last_audit"] = AuditRunRepo(stores.domain, config.project.id).last()
        result["memory"] = KnowledgeRepo(stores.domain, config.project.id).counts()
        try:
            from ..evidence.store import EvidenceStore
            from ..runtime import Runtime

            rt = Runtime(st.root, config, stores)
            feat = rt.features().active()
            if feat:
                ev_store = EvidenceStore(st.root, feat.feature_id)
                items = ev_store.list_items()
                verified, _ = ev_store.verify()
                result["feature"] = {
                    "id": feat.feature_id,
                    "name": feat.name,
                    "status": feat.status,
                    "branch": feat.branch,
                    "has_plan": bool(feat.plan),
                    "guardrails": feat.guardrails,
                    "evidence_count": len(items),
                    "evidence_verified": verified,
                }
        except Exception:
            pass

    def render(c: Console) -> None:
        head = str(result["head"])[:7]
        c.print(f"[bold]Project[/] {result['project']} · HEAD {head} · schema v{result['schema_version']}")
        audit = result["last_audit"]
        if audit:
            c.print(
                f"[bold]Audit[/]   {audit['mode']} at {audit['finished_at']} @ {str(audit['commit'])[:7]} · "
                f"{audit['stats'].get('files', 0)} files · {audit['stats'].get('symbols', 0)} symbols"
            )
        else:
            c.print("[bold]Audit[/]   none yet. Run: agent-factory audit")
        mem = result["memory"]
        if mem:
            parts = []
            for kind in ("pattern", "decision", "constraint"):
                counts = mem.get(kind, {})
                parts.append(f"{kind}s {sum(counts.values())} ({counts.get('validated', 0)} validated)")
            candidates = sum(v.get("candidate", 0) for v in mem.values())
            c.print("[bold]Memory[/]  " + " · ".join(parts) + f" · candidates {candidates}")
        else:
            c.print("[bold]Memory[/]  empty")
        feat = result.get("feature")
        if feat:
            c.print(f"[bold]Feature[/] {feat['id']} · {feat['status']} · branch {feat['branch'] or '-'}")
            plan_str = "plan ✓" if feat["has_plan"] else "plan ✗"
            gr = feat.get("guardrails")
            gr_str = f"guardrails {gr.get('status', 'not run')}" if gr else "guardrails not run"
            ev_str = (
                f"evidence {feat['evidence_count']} items ({'verified' if feat['evidence_verified'] else 'unverified'})"
            )
            c.print(f"         {plan_str} · {gr_str} · {ev_str}")

    emit(ctx, result, render)
