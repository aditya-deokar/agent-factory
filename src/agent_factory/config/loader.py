"""Load configuration: built-in defaults < agent-factory.yaml < AF_* env overrides.

`.env` in the repository root is loaded into the process environment (never
overriding variables that are already set), so connection settings resolve the
same way for the CLI, the MCP server and tests.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import ValidationError

from .model import CONFIG_FILENAME, AgentFactoryConfig, ConfigError, Neo4jTarget

_RESERVED_ENV = {"AF_CONFIG", "AF_LIVE_ENV_FILE"}


@dataclass(frozen=True)
class ConnectionSettings:
    uri: str
    username: str
    password: str
    database: str | None = None

    def describe(self) -> str:
        """Safe to print: the URI without credentials."""
        return f"{self.uri} (db={self.database or 'default'})"


def config_path(repo_root: Path) -> Path:
    override = os.environ.get("AF_CONFIG")
    return Path(override) if override else repo_root / CONFIG_FILENAME


def load_dotenv_for(repo_root: Path) -> bool:
    env_file = repo_root / ".env"
    return load_dotenv(env_file, override=False) if env_file.exists() else False


def load_config(repo_root: Path, *, env: Mapping[str, str] | None = None, dotenv: bool = True) -> AgentFactoryConfig:
    """Load and validate the config for `repo_root`. Raises ConfigError."""
    if dotenv:
        load_dotenv_for(repo_root)
    path = config_path(repo_root)
    if not path.exists():
        raise ConfigError(f"{path.name} not found in {repo_root}. Run: agent-factory init")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"{path.name} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path.name} must be a mapping at the top level")
    return parse_config(raw, env=os.environ if env is None else env)


def parse_config(raw: dict[str, Any], *, env: Mapping[str, str] | None = None) -> AgentFactoryConfig:
    data = apply_env_overrides(raw, env or {})
    try:
        return AgentFactoryConfig.model_validate(data)
    except ValidationError as error:
        raise ConfigError(_format_validation_error(error)) from error


def apply_env_overrides(raw: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    """`AF_PROJECT__NAME=x` sets project.name; values are parsed as YAML scalars/lists."""
    data: dict[str, Any] = _deep_copy(raw)
    for key, value in sorted(env.items()):
        if not key.startswith("AF_") or key in _RESERVED_ENV or key.startswith("AF_TEST_") or "__" not in key:
            continue
        parts = [p.lower() for p in key[3:].split("__") if p]
        node = data
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = yaml.safe_load(value) if value else value
    return data


def resolve_target(target: Neo4jTarget, env: Mapping[str, str] | None = None) -> ConnectionSettings | None:
    """Connection settings from the env vars a target names, or None if the URI is unset."""
    env = os.environ if env is None else env
    uri = (env.get(target.uri_env) or "").strip()
    if not uri:
        return None
    database = (env.get(target.database_env) or "").strip() or None if target.database_env else None
    return ConnectionSettings(
        uri=uri,
        username=env.get(target.user_env, "") or "",
        password=env.get(target.password_env, "") or "",
        database=database,
    )


def missing_env(target: Neo4jTarget, env: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    return [name for name in (target.uri_env, target.user_env, target.password_env) if not env.get(name)]


def _deep_copy(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _deep_copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_deep_copy(v) for v in value]
    return value


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for item in error.errors():
        loc = ".".join(str(p) for p in item["loc"]) or "config"
        msg = item["msg"]
        if item["type"] == "extra_forbidden":
            msg = f"unknown key '{loc.split('.')[-1]}' (check spelling)"
        lines.append(f"{loc}: {msg}")
    return "invalid agent-factory.yaml:\n  " + "\n  ".join(lines)
