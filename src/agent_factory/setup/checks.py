"""`agent-factory doctor`: verify the harness can work in this repository.

Each check returns pass / warn / fail with a fix hint. Secret values are never
included in results: only env var names and credential-free URIs.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from ..common.paths import is_dirty, is_tracked
from ..config import AgentFactoryConfig, ConfigError, load_config
from ..config.loader import missing_env, resolve_target
from ..db.stores import GraphStores, StoreUnavailable
from .files import MCP_SERVER_NAME

Status = Literal["pass", "warn", "fail"]

EXIT_OK, EXIT_FAILED, EXIT_CONFIG, EXIT_INFRA = 0, 1, 2, 3

_MCP_FILES = {
    "claude-code": (".mcp.json", "mcpServers"),
    "cursor": (".cursor/mcp.json", "mcpServers"),
    "vscode": (".vscode/mcp.json", "servers"),
}
_SKILL_DIRS = (".claude/skills", ".agents/skills", ".cursor/skills", "skills")


@dataclass
class CheckResult:
    name: str
    status: Status
    detail: str = ""
    hint: str = ""
    category: str = "general"


@dataclass
class DoctorReport:
    repo: str
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        failed = [c for c in self.checks if c.status == "fail"]
        if not failed:
            return EXIT_OK
        if any(c.category == "config" for c in failed):
            return EXIT_CONFIG
        if any(c.category == "infra" for c in failed):
            return EXIT_INFRA
        return EXIT_FAILED

    def summary(self) -> dict[str, int]:
        out = {"pass": 0, "warn": 0, "fail": 0}
        for c in self.checks:
            out[c.status] += 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "summary": self.summary(),
            "exit_code": self.exit_code,
            "checks": [asdict(c) for c in self.checks],
        }


StoreFactory = Callable[[AgentFactoryConfig], GraphStores]


def run_doctor(
    root: Path,
    env: Mapping[str, str] | None = None,
    store_factory: StoreFactory | None = None,
    online: bool = False,
) -> DoctorReport:
    env = os.environ if env is None else env
    report = DoctorReport(repo=str(root))
    add = report.checks.append

    add(
        CheckResult("python", "pass", sys.version.split()[0])
        if sys.version_info >= (3, 11)
        else CheckResult("python", "fail", sys.version.split()[0], "Install Python 3.11+", "config")
    )

    config: AgentFactoryConfig | None = None
    try:
        config = load_config(root, env=env)
        add(CheckResult("config", "pass", f"project '{config.project.id}'", category="config"))
    except ConfigError as error:
        add(CheckResult("config", "fail", str(error), "Run: agent-factory init", "config"))

    add(_check_env_tracked(root))
    add(_check_git(root))
    add(_check_ripgrep())

    if config is None:
        return report

    for name, target in (("domain", config.neo4j.domain), ("memory", config.neo4j.memory)):
        missing = missing_env(target, env)
        if not missing:
            add(CheckResult(f"env:{name}", "pass", f"{target.uri_env} set", category="config"))
        elif name == "memory" and missing == [target.uri_env, target.user_env, target.password_env]:
            add(
                CheckResult(
                    f"env:{name}",
                    "warn",
                    "not set: memory shares the domain database",
                    f"Set {target.uri_env} to use a separate memory workspace",
                    "config",
                )
            )
        else:
            add(
                CheckResult(
                    f"env:{name}",
                    "fail",
                    f"missing: {', '.join(missing)}",
                    "Add them to .env (see .env.example)",
                    "config",
                )
            )

    for agent in config.agents:
        add(_check_mcp_config(root, agent))
    add(_check_skills(root))

    domain_settings = resolve_target(config.neo4j.domain, env)
    if domain_settings is None:
        return report
    factory = store_factory or (lambda cfg: GraphStores.from_config(cfg, env))
    try:
        stores = factory(config)
    except StoreUnavailable as error:
        add(
            CheckResult("neo4j:domain", "fail", str(error), "Start Neo4j (docker compose up -d) or check .env", "infra")
        )
        return report
    try:
        _check_databases(stores, config, add)
    finally:
        stores.close()

    if online:
        add(_check_embeddings(config, env))
    return report


def _check_databases(stores: GraphStores, config: AgentFactoryConfig, add: Callable[[CheckResult], None]) -> None:
    from ..db.migrations import current_version, index_states, latest_version, vector_dimension_mismatches

    try:
        stores.domain.verify()
        info = stores.domain.server_info()
    except Exception as error:
        add(CheckResult("neo4j:domain", "fail", _safe_error(error), "Start Neo4j or check NEO4J_* in .env", "infra"))
        return
    if info.supports_vector_index():
        add(
            CheckResult(
                "neo4j:domain",
                "pass",
                f"{stores.domain.settings.describe()} · {info.agent} {info.edition}",
                category="infra",
            )
        )
    else:
        add(
            CheckResult("neo4j:domain", "fail", f"{info.agent} has no vector index support", "Use Neo4j 5.11+", "infra")
        )

    if stores.memory is not None and not stores.same_instance:
        try:
            stores.memory.verify()
            add(CheckResult("neo4j:memory", "pass", stores.memory.settings.describe(), category="infra"))
        except Exception as error:
            add(CheckResult("neo4j:memory", "fail", _safe_error(error), "Check MVP_NEO4J_* in .env", "infra"))
    else:
        add(
            CheckResult(
                "neo4j:memory", "warn", "same-instance mode (memory shares the domain database)", category="infra"
            )
        )

    latest = latest_version(config.embeddings.dimensions)
    current = current_version(stores.domain)
    if current >= latest:
        add(CheckResult("schema", "pass", f"version {current}", category="schema"))
    else:
        add(
            CheckResult(
                "schema", "warn", f"version {current} of {latest}", "Run: agent-factory memory migrate", "schema"
            )
        )
    if current:
        states = index_states(stores.domain)
        offline = [s.name for s in states if s.state != "ONLINE"]
        mismatches = vector_dimension_mismatches(stores.domain, config.embeddings.dimensions)
        if mismatches:
            add(
                CheckResult(
                    "indexes",
                    "fail",
                    "; ".join(mismatches),
                    "Drop the af_*_embedding indexes and re-run migrate + audit (re-embeds)",
                    "schema",
                )
            )
        elif offline:
            add(
                CheckResult(
                    "indexes", "warn", f"not online: {', '.join(offline)}", "Wait, then re-run doctor", "schema"
                )
            )
        else:
            n_vec = sum(1 for s in states if s.type == "VECTOR")
            n_ft = sum(1 for s in states if s.type == "FULLTEXT")
            add(
                CheckResult(
                    "indexes", "pass", f"{len(states)} online ({n_vec} vector, {n_ft} full-text)", category="schema"
                )
            )


def _check_env_tracked(root: Path) -> CheckResult:
    if (root / ".git").exists() and is_tracked(root, ".env"):
        return CheckResult(
            ".env", "fail", ".env is committed to git", "git rm --cached .env; add it to .gitignore", "security"
        )
    return CheckResult(".env", "pass", "not tracked by git", category="security")


def _check_git(root: Path) -> CheckResult:
    if not (root / ".git").exists():
        return CheckResult("git", "warn", "not a git repository root", "Run inside the repository root")
    if is_dirty(root):
        return CheckResult(
            "git",
            "warn",
            "working tree has uncommitted changes",
            "Audits read the working tree; commit for reproducible memory",
        )
    return CheckResult("git", "pass", "clean working tree")


def _check_ripgrep() -> CheckResult:
    if shutil.which("rg"):
        return CheckResult("ripgrep", "pass", "rg on PATH")
    return CheckResult("ripgrep", "warn", "rg not found (slower Python fallback is used)", "Install ripgrep")


def _check_mcp_config(root: Path, agent: str) -> CheckResult:
    if agent not in _MCP_FILES:
        return CheckResult(f"mcp:{agent}", "warn", "configured globally by the user", "See agent-factory init notes")
    rel, key = _MCP_FILES[agent]
    path = root / rel
    if not path.exists():
        return CheckResult(f"mcp:{agent}", "warn", f"{rel} missing", "Run: agent-factory init")
    try:
        server = (json.loads(path.read_text(encoding="utf-8")).get(key) or {}).get(MCP_SERVER_NAME)
    except (json.JSONDecodeError, AttributeError):
        return CheckResult(f"mcp:{agent}", "warn", f"{rel} is not valid JSON", "Fix or delete it, then run init")
    if not server:
        return CheckResult(f"mcp:{agent}", "warn", f"no '{MCP_SERVER_NAME}' server in {rel}", "Run: agent-factory init")
    if server.get("args", [])[:2] != ["mcp", "serve"]:
        return CheckResult(
            f"mcp:{agent}", "warn", f"{rel} does not run 'agent-factory mcp serve'", "Run: agent-factory init"
        )
    return CheckResult(f"mcp:{agent}", "pass", rel)


def _check_skills(root: Path) -> CheckResult:
    for rel in _SKILL_DIRS:
        base = root / rel
        if base.is_dir() and any((base / d / "SKILL.md").exists() for d in ("reuse-check", "memory-retrieval")):
            return CheckResult("skills", "pass", f"found in {rel}")
    return CheckResult(
        "skills",
        "warn",
        "Agent Factory skills not installed",
        "Run: agent-factory init --skills (or npx skills add aditya-deokar/agent-factory)",
    )


def _check_embeddings(config: AgentFactoryConfig, env: Mapping[str, str]) -> CheckResult:
    if not env.get("OPENAI_API_KEY"):
        return CheckResult("embeddings", "warn", "OPENAI_API_KEY not set", "Audits can run with --no-embed")
    try:
        from openai import OpenAI

        client = OpenAI(api_key=env["OPENAI_API_KEY"], base_url=env.get("OPENAI_BASE_URL") or None, timeout=20)
        out = client.embeddings.create(model=config.embeddings.model, input=["ping"])
        dims = len(out.data[0].embedding)
    except Exception as error:
        return CheckResult(
            "embeddings", "warn", _safe_error(error), "Check OPENAI_* in .env; audits can use --no-embed"
        )
    if dims != config.embeddings.dimensions:
        return CheckResult(
            "embeddings",
            "fail",
            f"model returns {dims} dims, config says {config.embeddings.dimensions}",
            "Fix embeddings.dimensions",
            "config",
        )
    return CheckResult("embeddings", "pass", f"{config.embeddings.model} ({dims}-d)")


def _safe_error(error: Exception) -> str:
    from ..common.redact import redact

    text = f"{type(error).__name__}: {error}"
    body = getattr(error, "body", None)
    if isinstance(body, dict) and body.get("message"):
        text = f"{type(error).__name__}: {body['message']}"
    return redact(text).text[:300]
