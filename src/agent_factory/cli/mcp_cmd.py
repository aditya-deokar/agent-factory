"""`agent-factory mcp serve`."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from .common import state

mcp_app = typer.Typer(help="Run the Agent Factory MCP server for coding agents.", no_args_is_help=True)


@mcp_app.command("serve")
def serve(
    ctx: typer.Context,
    http: Annotated[bool, typer.Option("--http", help="Streamable HTTP instead of stdio")] = False,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    """Serve the MCP tools (stdio by default: what Claude Code, Cursor and VS Code launch)."""
    from ..mcp.server import serve as run

    root = state(ctx).root
    if http:
        print(f"agent-factory MCP on http://{host}:{port}/mcp (repo {root})", file=sys.stderr)
    run(root, "streamable-http" if http else "stdio", host, port)
