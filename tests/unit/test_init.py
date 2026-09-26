import json
from pathlib import Path

import pytest
import yaml

from agent_factory.config import load_config
from agent_factory.setup.detect import detect_checks, detect_languages, slugify
from agent_factory.setup.files import AGENTS_END, AGENTS_START
from agent_factory.setup.init_project import InitOptions, run_init


def _snapshot(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def test_init_creates_expected_tree(repo: Path):
    result = run_init(repo, InitOptions())
    created = {a.path for a in result.actions if a.kind == "created"}
    assert {
        "agent-factory.yaml",
        ".mcp.json",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
        "AGENTS.md",
        ".gitignore",
    } <= created
    assert (repo / ".agent-factory/cache").is_dir()
    assert (repo / ".agent-factory/audit.log").exists()
    cfg = load_config(repo, env={}, dotenv=False)
    assert cfg.project.id == "shop"
    assert cfg.project.languages == ["typescript"]
    assert cfg.checks.test == "npm test"
    assert cfg.checks.build == "npm run build"
    vscode = json.loads((repo / ".vscode/mcp.json").read_text())
    assert vscode["servers"]["agent-factory"] == {"type": "stdio", "command": "agent-factory", "args": ["mcp", "serve"]}
    claude = json.loads((repo / ".mcp.json").read_text())
    assert claude["mcpServers"]["agent-factory"]["args"] == ["mcp", "serve"]


def test_init_is_idempotent(repo: Path):
    run_init(repo, InitOptions())
    before = _snapshot(repo)
    second = run_init(repo, InitOptions())
    assert {a.kind for a in second.actions} == {"unchanged"}
    assert _snapshot(repo) == before


def test_dry_run_writes_nothing(repo: Path):
    before = _snapshot(repo)
    result = run_init(repo, InitOptions(dry_run=True))
    assert any(a.kind == "created" for a in result.actions)
    assert _snapshot(repo) == before


def test_init_merges_existing_mcp_json(repo: Path):
    existing = {"mcpServers": {"neo4j-mcp": {"type": "http", "url": "https://x.mcp-instances.neo4j.io"}}}
    (repo / ".mcp.json").write_text(json.dumps(existing), encoding="utf-8")
    run_init(repo, InitOptions(agents=["claude-code"]))
    servers = json.loads((repo / ".mcp.json").read_text())["mcpServers"]
    assert servers["neo4j-mcp"] == existing["mcpServers"]["neo4j-mcp"]
    assert "agent-factory" in servers


def test_invalid_mcp_json_is_left_untouched(repo: Path):
    (repo / ".mcp.json").write_text("{not json", encoding="utf-8")
    result = run_init(repo, InitOptions(agents=["claude-code"]))
    action = next(a for a in result.actions if a.path == ".mcp.json")
    assert action.kind == "skipped"
    assert (repo / ".mcp.json").read_text() == "{not json"


def test_optional_servers(repo: Path):
    run_init(repo, InitOptions(agents=["vscode"], aura_instance="abc123", graphacademy=True))
    servers = json.loads((repo / ".vscode/mcp.json").read_text())["servers"]
    assert servers["neo4j-aura"]["url"] == "https://abc123.mcp-instances.neo4j.io"
    assert servers["neo4j-graphacademy"]["url"].endswith("/mcp")


def test_agents_md_marker_merge(repo: Path):
    (repo / "AGENTS.md").write_text("# Team rules\n\nKeep PRs small.\n", encoding="utf-8")
    run_init(repo, InitOptions())
    text = (repo / "AGENTS.md").read_text()
    assert text.startswith("# Team rules\n\nKeep PRs small.")
    assert text.count(AGENTS_START) == 1
    (repo / "AGENTS.md").write_text(text.replace("Keep PRs small.", "Keep PRs tiny."), encoding="utf-8")
    run_init(repo, InitOptions())
    text = (repo / "AGENTS.md").read_text()
    assert "Keep PRs tiny." in text
    assert text.count(AGENTS_START) == 1 and text.count(AGENTS_END) == 1


def test_existing_config_is_never_overwritten(repo: Path):
    (repo / "agent-factory.yaml").write_text(
        yaml.safe_dump({"project": {"id": "custom", "name": "C"}}), encoding="utf-8"
    )
    result = run_init(repo, InitOptions())
    assert result.project_id == "custom"
    assert yaml.safe_load((repo / "agent-factory.yaml").read_text())["project"]["id"] == "custom"


def test_gitignore_entries_deduplicated(repo: Path):
    (repo / ".gitignore").write_text("node_modules/\n.env\n", encoding="utf-8")
    run_init(repo, InitOptions())
    lines = (repo / ".gitignore").read_text().splitlines()
    assert lines.count(".env") == 1
    assert ".agent-factory/cache/" in lines


def test_codex_prints_snippet_instead_of_writing(repo: Path):
    result = run_init(repo, InitOptions(agents=["codex"]))
    assert any("[mcp_servers.agent-factory]" in n for n in result.notes)
    assert not (repo / ".codex").exists()


def test_unknown_agent_rejected(repo: Path):
    with pytest.raises(ValueError, match="unknown agents"):
        run_init(repo, InitOptions(agents=["notepad"]))


def test_detection_helpers(tmp_path: Path):
    files = ["src/a.ts", "src/b.tsx", "src/c.py", "node_modules/x/index.js", "types.d.ts", "d.js"]
    assert detect_languages(tmp_path, files) == ["typescript", "python", "javascript"]
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    checks = detect_checks(tmp_path)
    assert checks.test == "uv run pytest -q"
    assert checks.lint == "uv run ruff check ."
    assert slugify("My Cool_App!") == "my-cool-app"
