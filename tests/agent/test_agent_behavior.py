"""Behavioral test with a real coding agent (Phase 7). Opt-in: costs tokens and uses your Claude account.

    AF_AGENT_TESTS=1 AF_TEST_NEO4J_URI=bolt://localhost:7687 uv run pytest -m agent

Claude Code runs headless on a fresh copy of the teamapp fixture with the Agent Factory skills and MCP
server installed. Agents are non-deterministic: the scenario runs N=3 times and passes on >= 2.
Assertions are about tool-call order and presence, not wording. Transcripts are kept in tests/agent/runs/.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytestmark = pytest.mark.agent
ROOT = Path(__file__).resolve().parents[2]
RUNS = ROOT / "tests" / "agent" / "runs"
PROMPT = "Add team invitations. Only produce an implementation plan; do not edit any files."
N, NEEDED = 3, 2


def _enabled() -> bool:
    return os.environ.get("AF_AGENT_TESTS") == "1" and shutil.which("claude") is not None


@pytest.fixture(scope="module")
def prepared_repo(tmp_path_factory):
    if not _enabled():
        pytest.skip("set AF_AGENT_TESTS=1 and install the claude CLI to run agent tests")
    uri = os.environ.get("AF_TEST_NEO4J_URI")
    if not uri:
        pytest.skip("set AF_TEST_NEO4J_URI (agent tests need a Neo4j the MCP server can reach)")
    from tests.fixtures.build_teamapp import build_teamapp

    root = build_teamapp(tmp_path_factory.mktemp("agent") / "teamapp")
    env = {
        **os.environ,
        "NEO4J_URI": uri,
        "NEO4J_USERNAME": os.environ.get("AF_TEST_NEO4J_USER", "neo4j"),
        "NEO4J_PASSWORD": os.environ.get("AF_TEST_NEO4J_PASSWORD", "agentfactory"),
    }
    pid = f"agent{uuid.uuid4().hex[:6]}"
    run = [sys.executable, "-m", "agent_factory", "--repo", str(root)]
    subprocess.run([*run, "init", "--agents", "claude-code", "--project-id", pid], env=env, check=True)
    subprocess.run([*run, "audit", "--no-history"], env=env, check=True)
    skills_dir = root / ".claude" / "skills"
    for name in ("memory-retrieval", "feature-planning", "reuse-check", "implementation-workflow"):
        shutil.copytree(ROOT / "skills" / name, skills_dir / name)
    mcp = {
        "mcpServers": {
            "agent-factory": {
                "command": sys.executable,
                "args": ["-m", "agent_factory", "mcp", "serve"],
                "env": {k: env[k] for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")},
            }
        }
    }
    (root / ".mcp.json").write_text(json.dumps(mcp), encoding="utf-8")
    return root, env


def _tool_calls(stream: str) -> list[tuple[str, dict]]:
    calls = []
    for line in stream.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        for block in (event.get("message") or {}).get("content", []) if isinstance(event.get("message"), dict) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                calls.append((block.get("name", ""), block.get("input") or {}))
    return calls


def _one_run(root: Path, env: dict, i: int) -> tuple[bool, str]:
    out = subprocess.run(
        [
            "claude",
            "-p",
            PROMPT,
            "--output-format",
            "stream-json",
            "--verbose",
            "--allowedTools",
            "mcp__agent-factory__*",
            "Read",
            "Glob",
            "Grep",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{i}.jsonl").write_text(out.stdout, encoding="utf-8")
    calls = _tool_calls(out.stdout)
    names = [n for n, _ in calls]
    first_af = next((k for k, n in enumerate(names) if n.startswith("mcp__agent-factory__")), None)
    first_src_read = next(
        (k for k, (n, a) in enumerate(calls) if n in ("Read", "Edit") and "src" in json.dumps(a)), None
    )
    retrieval_first = first_af is not None and (first_src_read is None or first_af < first_src_read)
    reuse_checked = any(n.endswith("find_reusable") for n in names)
    mentions_token = "TokenService" in out.stdout.split('"type":"result"')[-1]
    ok = retrieval_first and reuse_checked and mentions_token
    return ok, f"retrieval_first={retrieval_first} reuse_checked={reuse_checked} tokenservice={mentions_token}"


def test_agent_retrieves_and_checks_reuse_before_planning(prepared_repo):
    root, env = prepared_repo
    results = [_one_run(root, env, i) for i in range(N)]
    passed = sum(ok for ok, _ in results)
    assert passed >= NEEDED, [detail for _, detail in results]
