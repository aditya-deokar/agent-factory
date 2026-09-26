"""Phase 7: skills are well-formed and every tool / command they mention exists."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import anyio
import pytest
import typer
import yaml
from mcp.client.client import Client

from agent_factory.cli.main import app
from agent_factory.mcp.server import build_server
from agent_factory.setup.init_project import AGENTS_SECTION, CURSOR_RULE

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "skills"
AGENT_FACTORY_SKILLS = {
    "project-audit",
    "memory-retrieval",
    "feature-planning",
    "reuse-check",
    "architecture-check",
    "implementation-workflow",
    "memory-update",
    "verification",
    "pr-evidence",
    "memory-commit",
}
PHASE_8_PENDING: set[tuple[str, ...]] = set()
TOOL_NAME = re.compile(r"`((?:get|find|impact|search|ask|how|start|record|propose|check|add|complete)_[a-z_]+)`")
MCP_REF = re.compile(r"mcp__agent-factory__([a-z_]+)")
CLI_REF = re.compile(r"agent-factory ([a-z][a-z-]*)(?: ([a-z][a-z-]*))?")


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n([\s\S]*?)\r?\n---", text)
    assert m, f"{path} has no frontmatter"
    return yaml.safe_load(m.group(1))


def _texts() -> dict[str, str]:
    out = {
        str(p.relative_to(ROOT)): p.read_text(encoding="utf-8")
        for name in AGENT_FACTORY_SKILLS
        for p in (SKILLS / name).rglob("*.md")
    }
    out["AGENTS_SECTION"] = AGENTS_SECTION
    out["CURSOR_RULE"] = CURSOR_RULE
    return out


def _mcp_tools() -> set[str]:
    server = build_server(lambda: None)  # type: ignore[arg-type,return-value]  # listing never opens the runtime

    async def run():
        async with Client(server) as c:
            return await c.list_tools()

    return {t.name for t in anyio.run(run).tools}


def _cli_commands() -> dict[str, set[str]]:
    click_app = typer.main.get_command(app)
    commands: dict[str, set[str]] = {}
    for name, cmd in click_app.commands.items():  # type: ignore[attr-defined]
        commands[name] = set(getattr(cmd, "commands", {}) or {})
    return commands


def test_all_agent_factory_skills_exist_and_are_well_formed():
    found = {p.parent.name for p in SKILLS.glob("*/SKILL.md")}
    assert found >= AGENT_FACTORY_SKILLS
    for name in AGENT_FACTORY_SKILLS:
        fm = _frontmatter(SKILLS / name / "SKILL.md")
        assert fm["name"] == name
        assert re.search(r"\b(use|apply|run)\s+(when|whenever|before)\b", fm["description"], re.I), name
        assert len(fm["description"]) <= 1024 and fm["license"] == "MIT"
        assert fm["metadata"]["author"] == "agent-factory" and fm["metadata"]["version"]
        assert fm.get("compatibility") and fm.get("allowed-tools")
        body = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2]
        assert len(body.splitlines()) <= 260, name


def test_every_mcp_tool_referenced_exists():
    tools = _mcp_tools()
    missing = {}
    for path, text in _texts().items():
        refs = set(MCP_REF.findall(text)) | set(TOOL_NAME.findall(text))
        if refs - tools:
            missing[path] = sorted(refs - tools)
    assert not missing, missing


def test_every_cli_command_referenced_exists_or_is_pending():
    commands = _cli_commands()
    unknown, pending_used = {}, set()
    for path, text in _texts().items():
        for top, sub in CLI_REF.findall(text):
            if top in commands and (not commands[top] or not sub or sub in commands[top]):
                continue
            key = next((k for k in ((top, sub), (top,)) if k in PHASE_8_PENDING), (top, sub) if sub else (top,))
            if key in PHASE_8_PENDING:
                pending_used.add(key)
                continue
            unknown.setdefault(path, []).append(" ".join(k for k in key if k))
    assert not unknown, unknown
    assert pending_used == PHASE_8_PENDING, "update PHASE_8_PENDING: a pending command landed or is no longer used"


def test_skill_lint_passes():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    out = subprocess.run([node, "scripts/lint-skills.mjs"], cwd=ROOT, capture_output=True, text=True, check=False)
    assert out.returncode == 0, out.stdout + out.stderr
    for name in AGENT_FACTORY_SKILLS:
        assert f"  warn   {name}:" not in out.stdout, out.stdout
