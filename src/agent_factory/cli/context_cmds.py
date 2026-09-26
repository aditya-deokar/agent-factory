"""`context`, `reuse`, `impact`, `ask`: inspection commands (the agent builds; these inform)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

import typer
from rich.console import Console

from .common import EXIT_CONFIG, emit, fail, state


@contextmanager
def runtime(ctx: typer.Context, need_audit: bool = True) -> Iterator[Any]:
    from ..runtime import NotAudited, Runtime

    st = state(ctx)
    config = st.config()
    with st.stores(config) as stores:
        rt = Runtime(st.root, config, stores)
        if need_audit:
            try:
                rt.require_audit()
            except NotAudited as error:
                fail(str(error), EXIT_CONFIG)
        yield rt


def context(
    ctx: typer.Context,
    request: Annotated[str, typer.Argument(help='The feature or change, e.g. "Add team invitations"')],
    budget: Annotated[int, typer.Option(help="Token budget for the pack")] = 4000,
    history: Annotated[bool, typer.Option("--history", help="Include deprecated and superseded knowledge")] = False,
) -> None:
    """Everything an agent should know before implementing REQUEST (spec §15)."""
    from ..context.pack import render_markdown

    with runtime(ctx) as rt:
        pack = rt.engine().build(request, budget=budget, include_history=history)
        pack.warnings += rt.warnings
    emit(ctx, pack, lambda c: c.print(render_markdown(pack), markup=False, highlight=False))


def reuse(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="The abstraction you are about to create, e.g. InvitationTokenService")],
    desc: Annotated[str, typer.Option("--desc", help="One line: what it would do")] = "",
    methods: Annotated[str, typer.Option(help="Comma-separated method names, e.g. create,validate,expire")] = "",
    role: Annotated[str | None, typer.Option(help="Service | Repository | Controller | ...")] = None,
    limit: int = 5,
) -> None:
    """Check whether NAME already exists before creating it (spec §16)."""
    from ..context.reuse import ProposedAbstraction, render_reuse

    proposal = ProposedAbstraction(
        name=name, description=desc, role=role, methods=[m.strip() for m in methods.split(",") if m.strip()]
    )
    with runtime(ctx) as rt:
        report = rt.reuse().find(proposal, limit=limit)
        report.warnings += rt.warnings
    emit(ctx, report, lambda c: c.print(render_reuse(report), markup=False, highlight=False))


def impact(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="Symbol name, Class.method, uid or file path")],
    depth: Annotated[int, typer.Option(min=1, max=4)] = 2,
) -> None:
    """What depends on TARGET: dependents, routes, tests, co-changed files, rules in force."""
    from ..context.impact import TargetNotFound, analyze, render_impact

    with runtime(ctx) as rt:
        try:
            report = analyze(rt.store, rt.project_id, target, depth)
        except TargetNotFound as error:
            fail(str(error))
    emit(ctx, report, lambda c: c.print(render_impact(report), markup=False, highlight=False))


def ask(
    ctx: typer.Context,
    question: Annotated[str, typer.Argument(help='e.g. "How many controllers depend on TeamService?"')],
    show_cypher: Annotated[bool, typer.Option("--show-cypher")] = False,
) -> None:
    """Ask the code graph a structured question (Text2Cypher, read-only by force)."""
    from ..context.ask import UnsafeQuery, graphrag_generator
    from ..context.ask import ask as run_ask

    with runtime(ctx) as rt:
        if not rt.config.llm.enabled:
            fail("llm.enabled is false in agent-factory.yaml; ask needs an LLM", EXIT_CONFIG)
        generate = graphrag_generator(rt.config.llm.model, rt.env.get("OPENAI_API_KEY"), rt.env.get("OPENAI_BASE_URL"))
        try:
            result = run_ask(rt.store, rt.project_id, question, generate)
        except UnsafeQuery as error:
            fail(str(error))
        except Exception as error:  # LLM/proxy errors explain themselves
            body = getattr(error, "body", None)
            fail(f"could not generate a query: {body.get('message') if isinstance(body, dict) else error}")

    def render(c: Console) -> None:
        if show_cypher:
            c.print(result.cypher, markup=False, style="dim")
        for row in result.rows:
            c.print(row, markup=False)
        if not result.rows:
            c.print("no rows")

    emit(ctx, result, render)
