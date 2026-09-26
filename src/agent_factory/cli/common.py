"""Shared CLI plumbing: global options, repo/config/store resolution, output, exit codes.

Every command builds a plain result (dict or dataclass) and hands it to
`emit()`, which prints JSON for agents (`--json`) or Rich output for people.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

import typer
from rich.console import Console

from ..common.paths import NotAGitRepo, find_git_root
from ..config import AgentFactoryConfig, ConfigError, load_config
from ..db.stores import GraphStores, StoreUnavailable

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_INFRA = 0, 1, 2, 3

console = Console()
err_console = Console(stderr=True)


@dataclass
class AppState:
    repo_opt: Path | None = None
    json: bool = False
    verbose: bool = False

    @property
    def root(self) -> Path:
        try:
            return find_git_root(self.repo_opt or Path.cwd())
        except NotAGitRepo as error:
            fail(str(error), EXIT_CONFIG)

    def config(self) -> AgentFactoryConfig:
        try:
            return load_config(self.root)
        except ConfigError as error:
            fail(str(error), EXIT_CONFIG)

    @contextmanager
    def stores(self, config: AgentFactoryConfig | None = None) -> Iterator[GraphStores]:
        cfg = config or self.config()
        try:
            stores = GraphStores.from_config(cfg)
        except StoreUnavailable as error:
            fail(str(error), EXIT_INFRA)
        try:
            try:
                stores.domain.verify()
            except StoreUnavailable as error:
                fail(
                    str(error) + "\nStart Neo4j (docker compose up -d) or check .env, then run agent-factory doctor",
                    EXIT_INFRA,
                )
            yield stores
        finally:
            stores.close()


def state(ctx: typer.Context) -> AppState:
    obj = ctx.find_root().obj
    if not isinstance(obj, AppState):
        obj = AppState()
        ctx.find_root().obj = obj
    return obj


def fail(message: str, code: int = EXIT_FAILED) -> NoReturn:
    err_console.print(f"[bold red]error:[/] {message}", highlight=False)
    raise typer.Exit(code)


def to_jsonable(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def emit(ctx: typer.Context, result: Any, render: Callable[[Console], None]) -> None:
    if state(ctx).json:
        typer.echo(json.dumps(to_jsonable(result), indent=2, default=str, ensure_ascii=False))
    else:
        render(console)


STATUS_STYLE = {
    "pass": "green",
    "warn": "yellow",
    "fail": "red",
    "created": "green",
    "updated": "cyan",
    "unchanged": "dim",
    "skipped": "yellow",
}
