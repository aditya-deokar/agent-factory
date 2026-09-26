"""`agent-factory init`: set up the harness in a repository. Idempotent.

It never builds anything and never deletes anything: it writes the config,
the local state folder, ignore rules, MCP configs for the chosen agents, and
an Agent Factory section in AGENTS.md.
"""

from __future__ import annotations

import os
import re
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
evidence-backed engineering memory in Neo4j, exposed through the `agent-factory` MCP server and CLI
(every CLI command accepts `--json`). You write the code; Agent Factory tells you what already exists,
which rules apply, and remembers what you learn.

### Workflow for every feature or non-trivial fix (skill: implementation-workflow)

1. **Retrieve memory** (memory-retrieval): `get_feature_context` / `agent-factory context "<request>"`.
2. **Isolate** (worktree-isolation): a fresh branch, never main.
3. **Plan** (feature-planning): `start_feature`, then `record_plan` / `agent-factory feature start|plan`.
4. **Check reuse** (reuse-check): `find_reusable` / `agent-factory reuse <Name>` for every new abstraction.
5. **Build** (service-layer), following the validated patterns cited in the plan.
6. **Record knowledge** (memory-update): `propose_memory` with `path:line` evidence.
7. **Check architecture** (architecture-check): constraints, duplication, dependencies, scope.
8. **Prove** (verification, test-evidence, visual-diff): run the checks, keep the evidence.
9. **Ship** (pr-evidence, code-review-loop): an evidence-backed PR body.
10. **Commit memory** (memory-commit): leave the repository smarter for the next feature.

### Guardrail questions before calling anything done

Duplication (does it already exist?) · Abstraction (is it necessary?) · Architecture (existing
boundaries?) · Reusability · Consistency (validated patterns?) · Complexity (new infrastructure?) ·
Scope (unrelated files?) · Regression (existing tests pass?).

Validated constraints are not yours to break: stop and ask. Candidates are hints, not rules.
"""

CLAUDE_SECTION = """\
@AGENTS.md
"""

CURSOR_RULE = """\
---
description: Agent Factory workflow - retrieve memory, check reuse, respect constraints, prove, commit memory
alwaysApply: true
---

Follow the "Agent Factory" section of AGENTS.md for every feature or non-trivial fix:
call get_feature_context before editing, find_reusable before creating any new class or module,
impact_of before changing a shared symbol, and propose_memory for durable findings with evidence.
Validated constraints are rules; candidates are hints.
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


def detect_aura_instance(root: Path) -> str | None:
    """Extract Aura instance ID from NEO4J_URI in environment or .env file."""
    uri = os.environ.get("NEO4J_URI", "")
    if not uri and (root / ".env").exists():
        try:
            for line in (root / ".env").read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("NEO4J_URI="):
                    uri = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass
    if uri:
        m = re.search(r"neo4j\+s://([a-zA-Z0-9]+)\.databases\.neo4j\.io", uri)
        if m:
            return m.group(1)
    return None


def run_init(root: Path, opts: InitOptions) -> InitResult:
    unknown = sorted(set(opts.agents) - set(SUPPORTED_AGENTS))
    if unknown:
        raise ValueError(f"unknown agents {unknown}; supported: {list(SUPPORTED_AGENTS)}")
    actions: list[Action] = []
    notes: list[str] = []
    dry = opts.dry_run

    if not opts.aura_instance:
        detected = detect_aura_instance(root)
        if detected:
            opts.aura_instance = detected
            notes.append(f"Auto-detected hosted Neo4j Aura instance '{detected}'; added neo4j-aura MCP endpoint.")

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
    actions.append(merge_marked_section(root / "CLAUDE.md", "CLAUDE.md", CLAUDE_SECTION, dry))
    if "cursor" in opts.agents:
        actions.append(
            write_text(root / ".cursor/rules/agent-factory.mdc", CURSOR_RULE, ".cursor/rules/agent-factory.mdc", dry)
        )

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
