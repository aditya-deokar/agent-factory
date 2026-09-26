"""`agent-factory init`: set up the harness in a repository. Idempotent.

It never builds anything and never deletes anything: it writes the config,
the local state folder, ignore rules, MCP configs for the chosen agents, and
an Agent Factory section in AGENTS.md.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config.model import CONFIG_FILENAME, SUPPORTED_AGENTS, AgentFactoryConfig, ProjectConfig
from .detect import detect_checks, detect_languages, slugify
from .files import (
    GRAPHACADEMY_URL,
    MCP_SERVER_NAME,
    STDIO_SERVER,
    Action,
    append_lines,
    ensure_dir,
    ensure_file,
    merge_marked_section,
    merge_mcp_json,
    write_text,
)

STATE_DIR = ".agent-factory"
GITIGNORE_HEADER = "# Agent Factory local state (agent-factory.yaml is committed)"
GITIGNORE_LINES = [
    ".env",
    ".agent-factory/cache/",
    ".agent-factory/evidence/",
    ".agent-factory/audit.log",
]
SKILLS_SOURCE = "aditya-deokar/agent-factory"

AGENTS_SECTION = """\
## Agent Factory

This repository uses [Agent Factory](https://github.com/aditya-deokar/agent-factory): persistent,
evidence-backed engineering memory in Neo4j, exposed through the `agent-factory` MCP server and CLI.

Before changing code:

1. Retrieve project memory for the request (`get_feature_context`, or `agent-factory context "<request>" --json`).
2. Check for reusable implementations before creating any new class, service, hook, component or module
   (`find_reusable`, or `agent-factory reuse <Name> --json`).
3. Respect validated constraints and decisions (`get_constraints`, or `agent-factory memory list --status validated`).

While and after changing code: record durable findings as memory candidates with evidence, run the
guardrails, and leave the repository's memory richer for the next feature.
"""

CODEX_SNIPPET = """\
# ~/.codex/config.toml  (user-global; add it yourself)
[mcp_servers.agent-factory]
command = "agent-factory"
args = ["mcp", "serve"]"""


@dataclass
class InitOptions:
    agents: list[str] = field(default_factory=lambda: ["claude-code", "cursor", "vscode"])
    dry_run: bool = False
    install_skills: bool = False
    aura_instance: str | None = None
    graphacademy: bool = False
    project_id: str | None = None


@dataclass
class InitResult:
    repo: str
    project_id: str
    actions: list[Action]
    notes: list[str]
    next_steps: list[str]
    dry_run: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "project_id": self.project_id,
            "dry_run": self.dry_run,
            "actions": [a.__dict__ for a in self.actions],
            "notes": self.notes,
            "next_steps": self.next_steps,
        }


def build_default_config(root: Path, project_id: str | None = None) -> AgentFactoryConfig:
    root = root.resolve()
    languages = [lang for lang in detect_languages(root) if lang in ("typescript", "javascript", "python")]
    return AgentFactoryConfig(
        project=ProjectConfig(id=project_id or slugify(root.name), name=root.name, languages=languages),
        checks=detect_checks(root),
    )


def render_config(config: AgentFactoryConfig) -> str:
    data = config.model_dump(mode="json", exclude_none=False)
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=None, width=110)
    return (
        "# Agent Factory configuration: commit this file.\n"
        "# Secrets are NOT stored here: *_env keys name environment variables (see .env).\n" + body
    )


def _mcp_servers(opts: InitOptions, vscode: bool) -> dict[str, Any]:
    servers: dict[str, Any] = {MCP_SERVER_NAME: ({"type": "stdio", **STDIO_SERVER} if vscode else dict(STDIO_SERVER))}
    if opts.aura_instance:
        servers["neo4j-aura"] = {"type": "http", "url": f"https://{opts.aura_instance}.mcp-instances.neo4j.io"}
    if opts.graphacademy:
        servers["neo4j-graphacademy"] = {"type": "http", "url": GRAPHACADEMY_URL}
    return servers


def run_init(root: Path, opts: InitOptions) -> InitResult:
    unknown = sorted(set(opts.agents) - set(SUPPORTED_AGENTS))
    if unknown:
        raise ValueError(f"unknown agents {unknown}; supported: {list(SUPPORTED_AGENTS)}")
    actions: list[Action] = []
    notes: list[str] = []
    dry = opts.dry_run

    cfg_path = root / CONFIG_FILENAME
    if cfg_path.exists():
        existing = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        project_id = str((existing.get("project") or {}).get("id") or opts.project_id or slugify(root.name))
        suggested = build_default_config(root, project_id).model_dump(mode="json")
        missing = sorted(k for k in suggested if k not in existing)
        detail = f"kept; missing sections use defaults: {', '.join(missing)}" if missing else "kept"
        actions.append(Action(CONFIG_FILENAME, "unchanged", detail))
    else:
        config = build_default_config(root, opts.project_id)
        project_id = config.project.id
        actions.append(write_text(cfg_path, render_config(config), CONFIG_FILENAME, dry))

    for sub in ("cache", "evidence", "features"):
        actions.append(ensure_dir(root / STATE_DIR / sub, f"{STATE_DIR}/{sub}/", dry))
    actions.append(ensure_file(root / STATE_DIR / "audit.log", f"{STATE_DIR}/audit.log", dry))
    actions.append(append_lines(root / ".gitignore", GITIGNORE_LINES, GITIGNORE_HEADER, ".gitignore", dry))

    if "claude-code" in opts.agents:
        actions.append(merge_mcp_json(root / ".mcp.json", ".mcp.json", "mcpServers", _mcp_servers(opts, False), dry))
    if "cursor" in opts.agents:
        actions.append(
            merge_mcp_json(root / ".cursor/mcp.json", ".cursor/mcp.json", "mcpServers", _mcp_servers(opts, False), dry)
        )
    if "vscode" in opts.agents:
        actions.append(
            merge_mcp_json(root / ".vscode/mcp.json", ".vscode/mcp.json", "servers", _mcp_servers(opts, True), dry)
        )
    if "codex" in opts.agents:
        notes.append("Codex reads a user-global config. Add this yourself:\n" + CODEX_SNIPPET)

    actions.append(merge_marked_section(root / "AGENTS.md", "AGENTS.md", AGENTS_SECTION, dry, title="# Agents"))

    if opts.install_skills:
        actions.append(_install_skills(root, dry))

    return InitResult(
        repo=str(root),
        project_id=project_id,
        actions=actions,
        notes=notes,
        next_steps=["agent-factory doctor", "agent-factory audit"],
        dry_run=dry,
    )


def _install_skills(root: Path, dry_run: bool) -> Action:
    npx = shutil.which("npx")
    if npx is None:
        return Action(
            "skills", "skipped", "npx not found; install Node.js 18+ then run: npx skills add " + SKILLS_SOURCE
        )
    if dry_run:
        return Action("skills", "updated", f"would run: npx skills add {SKILLS_SOURCE}")
    result = subprocess.run(
        [npx, "--yes", "skills", "add", SKILLS_SOURCE],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:] or ["unknown error"]
        return Action("skills", "skipped", f"npx skills add failed: {tail[0]}")
    return Action("skills", "updated", f"installed from {SKILLS_SOURCE}")
