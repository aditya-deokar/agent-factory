"""`agent-factory memory ...`: inspect and manage project memory."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .common import AppState, emit, fail, state

memory_app = typer.Typer(help="Inspect and manage project memory (knowledge, schema, audit log).", no_args_is_help=True)

_STATUS_STYLE = {
    "validated": "green",
    "candidate": "yellow",
    "deprecated": "dim",
    "superseded": "dim",
    "rejected": "red",
}


# -- schema (Phase 2) ---------------------------------------------------------------------------------------------


@memory_app.command("migrate")
def migrate(ctx: typer.Context) -> None:
    """Create or upgrade the graph schema (constraints, full-text and vector indexes)."""
    from ..db.migrations import apply_migrations

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        report = apply_migrations(stores.domain, config.embeddings.dimensions)

    def render(c: Console) -> None:
        if report.applied:
            c.print(f"[green]Applied[/] {', '.join(report.applied)} → schema v{report.to_version}")
        else:
            c.print(f"Schema already at v{report.to_version}; nothing to do.")

    emit(ctx, report, render)


@memory_app.command("schema")
def schema(
    ctx: typer.Context,
    md: Annotated[bool, typer.Option("--md", help="Print Markdown (for docs/graph-schema.md)")] = False,
    out: Annotated[Path | None, typer.Option(help="Write the Markdown to this file")] = None,
) -> None:
    """Describe the graph schema with live counts for this project."""
    from ..schema.doc import schema_markdown

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        text = schema_markdown(stores.domain, config.project.id)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8", newline="\n")
    emit(
        ctx,
        {"markdown": text, "written_to": str(out) if out else None},
        lambda c: c.print(text, markup=False, highlight=False) if md or not out else c.print(f"wrote {out}"),
    )


# -- knowledge lifecycle (Phase 4) --------------------------------------------------------------------------------


def _service(st: AppState, stores: Any, config: Any) -> Any:
    from ..memory.knowledge import KnowledgeService

    return KnowledgeService(stores.domain, config.project.id, st.root, config.validation)


def _resolve_uid(repo: Any, project_id: str, ref: str) -> str:
    """Accept a full uid, a unique uid suffix, or a unique title fragment."""
    if ref.startswith(f"{project_id}:k:") and repo.get(ref):
        return ref
    items = repo.list_knowledge(limit=10_000)
    exact = [k for k in items if k["uid"].endswith(f":{ref}") or k["uid"] == ref]
    in_id = [k for k in items if ref.lower() in k["uid"].split(":k:", 1)[-1]]
    matches = exact or in_id or [k for k in items if ref.lower() in k["title"].lower()]
    if len(matches) == 1:
        return str(matches[0]["uid"])
    if not matches:
        fail(f"no knowledge matches {ref!r}")
    fail(f"{ref!r} matches {len(matches)} items; use a uid:\n  " + "\n  ".join(m["uid"] for m in matches[:10]))


def _actor(st: AppState) -> str:
    from ..memory.knowledge import human_actor

    return human_actor(st.root)


@memory_app.command("list")
def list_cmd(
    ctx: typer.Context,
    kind: Annotated[str | None, typer.Option(help="pattern | decision | constraint")] = None,
    status: Annotated[
        str | None, typer.Option(help="candidate | validated | deprecated | superseded | rejected")
    ] = None,
    limit: int = 200,
) -> None:
    """List knowledge with status, confidence and support."""
    from ..db.repos import KnowledgeRepo

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        items = KnowledgeRepo(stores.domain, config.project.id).list_knowledge(kind=kind, status=status, limit=limit)

    def render(c: Console) -> None:
        t = Table(title=f"{len(items)} knowledge items")
        for col in ("status", "kind", "title", "conf", "support", "viol", "id"):
            t.add_column(col, overflow="fold")
        for k in items:
            t.add_row(
                f"[{_STATUS_STYLE.get(k['status'], '')}]{k['status']}[/]",
                k["kind"],
                k["title"],
                str(k.get("confidence", "")),
                str(k.get("support_count", "")),
                str(k.get("violation_count", "")),
                k["uid"].split(":k:", 1)[-1],
            )
        c.print(t)

    emit(ctx, items, render)


@memory_app.command("show")
def show(ctx: typer.Context, ref: Annotated[str, typer.Argument(help="uid, id suffix or title fragment")]) -> None:
    """A knowledge item with its evidence (path:line) and history."""
    from ..db.repos import KnowledgeRepo

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        repo = KnowledgeRepo(stores.domain, config.project.id)
        uid = _resolve_uid(repo, config.project.id, ref)
        item = repo.get(uid) or {}
        evidence = repo.evidence_of(uid)
        events = repo.events(uid)
    result = {"knowledge": item, "evidence": evidence, "events": events}

    def render(c: Console) -> None:
        c.print(
            f"[bold]{item.get('title')}[/]  ({item.get('kind')}, {item.get('status')}, "
            f"confidence {item.get('confidence')}, support {item.get('support_count')}, "
            f"violations {item.get('violation_count')})",
            highlight=False,
        )
        c.print(f"[dim]{uid}[/]")
        c.print(item.get("claim", ""), markup=False)
        if item.get("rule"):
            c.print(f"Rule: {item.get('rule_type')} {item.get('rule')}", markup=False)
        if item.get("status_reason"):
            c.print(f"Reason: {item.get('status_reason')}", markup=False)
        for e in evidence:
            loc = ""
            if e.get("line_start"):
                loc = f":{e['line_start']}"
                if e.get("line_end") and e["line_end"] != e["line_start"]:
                    loc += f"-{e['line_end']}"
            mark = "✓" if e["rel"] == "SUPPORTED_BY" else "✗"
            stale = " (stale)" if e.get("stale") else ""
            c.print(f"  {mark} {e.get('path')}{loc}{stale}  {e.get('note') or ''}", markup=False)
        if events:
            c.print("History:")
            for ev in reversed(events):
                c.print(
                    f"  {ev['at'][:19]} {ev['actor']}: {ev['action']} "
                    f"{ev.get('from_status') or ''}→{ev.get('to_status') or ''} {ev.get('reason') or ''}",
                    markup=False,
                )

    emit(ctx, result, render)


def _lifecycle(ctx: typer.Context, ref: str, action: str, reason: str | None, by: str | None = None) -> None:
    from ..db.repos import KnowledgeRepo
    from ..memory.lifecycle import IllegalTransition

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        repo = KnowledgeRepo(stores.domain, config.project.id)
        uid = _resolve_uid(repo, config.project.id, ref)
        service = _service(st, stores, config)
        actor = _actor(st)
        try:
            if action == "approve":
                item = service.approve(uid, actor, reason)
            elif action == "reject":
                item = service.reject(uid, actor, reason or "")
            elif action == "deprecate":
                item = service.deprecate(uid, actor, reason or "")
            else:
                item = service.supersede(uid, _resolve_uid(repo, config.project.id, by or ""), actor, reason)
        except (IllegalTransition, ValueError, KeyError) as error:
            fail(str(error))
    emit(ctx, item, lambda c: c.print(f"{item['title']}: [bold]{item['status']}[/] ({actor})"))


@memory_app.command("approve")
def approve(ctx: typer.Context, ref: str, reason: Annotated[str | None, typer.Option()] = None) -> None:
    """Approve a candidate: it becomes validated guidance for agents."""
    _lifecycle(ctx, ref, "approve", reason)


@memory_app.command("reject")
def reject(ctx: typer.Context, ref: str, reason: Annotated[str, typer.Option(help="Why (required)")]) -> None:
    """Reject a candidate. It will not be re-suggested without new evidence."""
    _lifecycle(ctx, ref, "reject", reason)


@memory_app.command("deprecate")
def deprecate(ctx: typer.Context, ref: str, reason: Annotated[str, typer.Option(help="Why (required)")]) -> None:
    """Retire validated knowledge that no longer reflects the codebase."""
    _lifecycle(ctx, ref, "deprecate", reason)


@memory_app.command("supersede")
def supersede(
    ctx: typer.Context,
    ref: str,
    by: Annotated[str, typer.Option(help="The replacement knowledge")],
    reason: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Mark knowledge as replaced by another item (adds a SUPERSEDES edge)."""
    _lifecycle(ctx, ref, "supersede", reason, by)


@memory_app.command("propose")
def propose(
    ctx: typer.Context,
    kind: Annotated[str, typer.Option(help="pattern | decision | constraint")],
    title: Annotated[str, typer.Option()],
    claim: Annotated[str, typer.Option()],
    evidence: Annotated[list[str], typer.Option(help="path[:start[-end]], repeatable")],
    rationale: Annotated[str | None, typer.Option()] = None,
    source: Annotated[str, typer.Option(help="human | agent")] = "human",
    rule: Annotated[
        str | None, typer.Option(help='Constraint rule as JSON, e.g. {"type": "forbid_dependency", ...}')
    ] = None,
) -> None:
    """Propose knowledge with evidence. It is validated like everything else."""
    from pydantic import ValidationError

    from ..memory.validation import EvidenceRef, Proposal

    st = state(ctx)
    config = st.config()
    rule_type, rule_body = None, None
    if rule:
        parsed = json.loads(rule)
        rule_type = parsed.pop("type", None)
        rule_body = parsed
    try:
        proposal = Proposal.model_validate(
            {
                "kind": kind,
                "title": title,
                "claim": claim,
                "source": source,
                "rationale": rationale,
                "evidence": [EvidenceRef.parse(e) for e in evidence],
                "rule_type": rule_type,
                "rule": rule_body,
            }
        )
    except (ValidationError, ValueError) as error:
        fail(str(error))
    with st.stores(config) as stores:
        actor = _actor(st) if source == "human" else "agent:cli"
        result = _service(st, stores, config).submit(proposal, actor)
    emit(
        ctx,
        result,
        lambda c: c.print(
            f"{result.outcome}: {result.uid} → {result.status} (confidence {result.confidence})"
            + "".join(f"\n  - {r}" for r in result.reasons + result.dropped_evidence),
            markup=False,
        ),
    )
    if result.outcome == "rejected":
        raise typer.Exit(1)


def lucene_escape(text: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/]|&&|\|\|)', r"\\\1", text)


@memory_app.command("search")
def search(ctx: typer.Context, query: str, limit: int = 10) -> None:
    """Keyword search over knowledge titles and claims."""
    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        rows = stores.domain.read(
            "CALL db.index.fulltext.queryNodes('af_knowledge_text', $q) YIELD node, score "
            "WHERE node.project_id = $p RETURN node.uid AS uid, node.title AS title, node.status AS status, "
            "node.kind AS kind, round(score, 3) AS score LIMIT $limit",
            q=lucene_escape(query),
            p=config.project.id,
            limit=limit,
        )

    def render(c: Console) -> None:
        if not rows:
            c.print("no matches")
        for r in rows:
            c.print(f"{r['score']:>6} {r['status']:<10} {r['kind']:<10} {r['title']}", markup=False)

    emit(ctx, rows, render)


@memory_app.command("log")
def log(
    ctx: typer.Context,
    ref: Annotated[str | None, typer.Argument()] = None,
    since: Annotated[str | None, typer.Option(help="ISO date, e.g. 2026-09-01")] = None,
    limit: int = 50,
) -> None:
    """The audit trail of memory changes."""
    from ..db.repos import KnowledgeRepo

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        repo = KnowledgeRepo(stores.domain, config.project.id)
        uid = _resolve_uid(repo, config.project.id, ref) if ref else None
        events = repo.events(uid, since, limit)

    def render(c: Console) -> None:
        for e in events:
            c.print(
                f"{e['at'][:19]} {e['actor']:<18} {e['action']:<13} {e.get('from_status') or '-':>10} → "
                f"{e.get('to_status') or '-':<10} {e['target_uid'].split(':k:', 1)[-1]} {e.get('reason') or ''}",
                markup=False,
            )

    emit(ctx, events, render)


@memory_app.command("export")
def export(
    ctx: typer.Context,
    out: Annotated[Path | None, typer.Option()] = None,
    fmt: Annotated[str, typer.Option("--format", help="json | md")] = "json",
    everything: Annotated[bool, typer.Option("--all", help="Include the code graph, not only knowledge")] = False,
) -> None:
    """Export project memory (knowledge + evidence + audit log) for review or backup."""
    from ..db.repos import AdminRepo, KnowledgeRepo

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        labels = None if everything else ["Knowledge", "Evidence", "MemoryEvent"]
        data = AdminRepo(stores.domain, config.project.id).export(labels=labels)
        items = KnowledgeRepo(stores.domain, config.project.id).list_knowledge(limit=10_000)
    text = json.dumps(data, indent=1, default=str) if fmt == "json" else knowledge_markdown(items)
    if out:
        out.write_text(text, encoding="utf-8", newline="\n")
    summary = {
        "written_to": str(out) if out else None,
        "nodes": len(data["nodes"]),
        "relationships": len(data["relationships"]),
    }
    emit(
        ctx,
        summary if out else data,
        lambda c: c.print(f"wrote {out} ({len(data['nodes'])} nodes)") if out else c.print(text, markup=False),
    )


def knowledge_markdown(items: list[dict[str, Any]]) -> str:
    lines = ["# Project memory", ""]
    for kind in ("decision", "constraint", "pattern"):
        group = [k for k in items if k["kind"] == kind]
        if not group:
            continue
        lines += [f"## {kind.capitalize()}s", "", "| Status | Title | Confidence | Support |", "|---|---|---|---|"]
        lines += [f"| {k['status']} | {k['title']} | {k.get('confidence')} | {k.get('support_count')} |" for k in group]
        lines.append("")
    return "\n".join(lines)


@memory_app.command("import")
def import_cmd(ctx: typer.Context, path: Path) -> None:
    """Import a JSON export (same project id)."""
    from ..db.repos import AdminRepo

    st = state(ctx)
    config = st.config()
    data = json.loads(path.read_text(encoding="utf-8"))
    with st.stores(config) as stores:
        try:
            result = AdminRepo(stores.domain, config.project.id).import_(data)
        except ValueError as error:
            fail(str(error))
    emit(ctx, result, lambda c: c.print(f"imported {result['nodes']} nodes, {result['relationships']} relationships"))


@memory_app.command("delete")
def delete(ctx: typer.Context, ref: str, yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    """Permanently delete one knowledge item and its orphaned evidence (logged)."""
    from ..db.repos import KnowledgeRepo

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        repo = KnowledgeRepo(stores.domain, config.project.id)
        uid = _resolve_uid(repo, config.project.id, ref)
        if not yes:
            typer.confirm(f"Delete {uid}?", abort=True)
        _service(st, stores, config).delete(uid, _actor(st))
    emit(ctx, {"deleted": uid}, lambda c: c.print(f"deleted {uid}"))


@memory_app.command("purge")
def purge(
    ctx: typer.Context,
    project: Annotated[str, typer.Option(help="Project id to purge (must match this repository)")],
    yes: Annotated[bool, typer.Option("--yes")] = False,
) -> None:
    """Delete ALL memory for this project from the graph (code graph, knowledge, history)."""
    from ..db.repos import AdminRepo

    st = state(ctx)
    config = st.config()
    if project != config.project.id:
        fail(f"--project must equal this repository's project id ({config.project.id})")
    if not yes:
        typer.confirm(f"Delete every node of project {project}?", abort=True)
    with st.stores(config) as stores:
        n = AdminRepo(stores.domain, project).purge_project()
    emit(ctx, {"deleted_nodes": n}, lambda c: c.print(f"deleted {n} nodes"))


@memory_app.command("review")
def review(ctx: typer.Context) -> None:
    """Step through candidates: approve, reject, or skip each one."""
    from ..db.repos import KnowledgeRepo

    st = state(ctx)
    if st.json:
        fail("review is interactive; use 'memory list --status candidate --json' with approve/reject instead")
    config = st.config()
    with st.stores(config) as stores:
        repo = KnowledgeRepo(stores.domain, config.project.id)
        service = _service(st, stores, config)
        actor = _actor(st)
        for k in repo.list_knowledge(status="candidate", limit=10_000):
            typer.echo(
                f"\n{k['kind'].upper()}: {k['title']}  (confidence {k.get('confidence')}, "
                f"support {k.get('support_count')}, violations {k.get('violation_count')})"
            )
            typer.echo(k["claim"])
            for e in repo.evidence_of(k["uid"])[:6]:
                mark = "✓" if e["rel"] == "SUPPORTED_BY" else "✗"
                typer.echo(f"   {mark} {e.get('path')}:{e.get('line_start') or ''}")
            choice = typer.prompt("[a]pprove / [r]eject / [s]kip / [q]uit", default="s").strip().lower()
            if choice == "q":
                break
            if choice == "a":
                service.approve(k["uid"], actor, "reviewed")
            elif choice == "r":
                service.reject(k["uid"], actor, typer.prompt("reason"))
