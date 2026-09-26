"""The `agent-factory` command.

The CLI manages the harness (setup, memory, audits, evidence). It never builds
features: the connected coding agent does that (spec §27).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from .. import __version__
from .audit_cmd import audit
from .common import AppState
from .memory_cmds import memory_app
from .setup_cmds import doctor, init, status

app = typer.Typer(
    name="agent-factory",
    help="Persistent engineering memory, context and guardrails for the coding agent you already use.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_show_locals=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"agent-factory {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    repo: Annotated[Path | None, typer.Option("--repo", help="Repository path (default: git root of cwd)")] = None,
    json_out: Annotated[bool, typer.Option("--json", help="Machine-readable JSON output (for agents)")] = False,
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
    version: Annotated[bool, typer.Option("--version", callback=_version, is_eager=True)] = False,
) -> None:
    ctx.obj = AppState(repo_opt=repo, json=json_out, verbose=verbose)


app.command()(init)
app.command()(doctor)
app.command()(status)
app.command()(audit)
app.add_typer(memory_app, name="memory")


_GLOBAL_FLAGS = ("--json", "--verbose", "-v")


def normalize_argv(argv: list[str]) -> list[str]:
    """Let global flags appear anywhere: `agent-factory reuse X --json` == `agent-factory --json reuse X`.

    Agents write the flag after the command; Typer only accepts it before. Arguments after `--` are left alone.
    """
    head, sep, tail = (
        (argv[: argv.index("--")], ["--"], argv[argv.index("--") + 1 :]) if "--" in argv else (argv, [], [])
    )
    flags = [a for a in head if a in _GLOBAL_FLAGS]
    rest = [a for a in head if a not in _GLOBAL_FLAGS]
    return flags + rest + sep + tail


def run() -> None:  # pragma: no cover - console entry
    if sys.platform == "win32":
        # Rich tables use box characters; make sure a legacy console code page does not crash output.
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    app(args=normalize_argv(sys.argv[1:]), prog_name="agent-factory")
