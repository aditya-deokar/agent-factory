"""Unit tests for MCP server aliases and Aura MCP auto-detection."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest
from mcp.client.client import Client

from agent_factory.context.pack import ContextPack
from agent_factory.context.token_economy import compute_token_economy
from agent_factory.mcp.server import build_server
from agent_factory.memory.agent_memory import TraceSummary
from agent_factory.setup.init_project import detect_aura_instance


def test_detect_aura_instance(tmp_path, monkeypatch):
    # 1. Detected from env var
    monkeypatch.setenv("NEO4J_URI", "neo4j+s://a1b2c3d4.databases.neo4j.io")
    assert detect_aura_instance(tmp_path) == "a1b2c3d4"

    # 2. Detected from .env file when env var is unset
    monkeypatch.delenv("NEO4J_URI", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text('NEO4J_URI="neo4j+s://z9y8x7w6.databases.neo4j.io"\n', encoding="utf-8")
    assert detect_aura_instance(tmp_path) == "z9y8x7w6"

    # 3. None when local bolt URI
    env_file.write_text("NEO4J_URI=bolt://localhost:7687\n", encoding="utf-8")
    assert detect_aura_instance(tmp_path) is None


def test_mcp_tool_listing_has_aliases():
    server = build_server(lambda: None)  # type: ignore[arg-type,return-value]

    async def run():
        async with Client(server) as c:
            return await c.list_tools()

    tools = {t.name for t in anyio.run(run).tools}
    assert "get_memory_context" in tools
    assert "how_did_i_handle" in tools
    assert "get_token_savings" in tools


def test_mcp_aliases_and_token_savings_execution():
    mock_rt = MagicMock()
    mock_rt.require_audit.return_value = None
    mock_rt.warnings = []

    # Setup memory
    mock_rt.memory.similar_traces = AsyncMock(
        return_value=[TraceSummary("add invitations", "reused TokenService", True, [])]
    )
    mock_rt.memory.enabled = True

    # Setup context engine
    pack = ContextPack(request="Add team invitations", project_id="test")
    pack.token_economy = compute_token_economy(total_repo_files=50, targeted_files=3, graph_pack_tokens=3000)
    mock_rt.engine().abuild = AsyncMock(return_value=pack)

    server = build_server(lambda: mock_rt)

    async def call_tool(name: str, args: dict):
        async with Client(server) as c:
            return await c.call_tool(name, args)

    # 1. Test how_did_i_handle alias
    res1 = anyio.run(call_tool, "how_did_i_handle", {"task": "invitations"})
    assert not res1.is_error
    data1 = json.loads(res1.content[0].text)
    assert "reused TokenService" in data1["summary"]

    # 2. Test get_memory_context alias
    res2 = anyio.run(call_tool, "get_memory_context", {"request": "Add team invitations"})
    assert not res2.is_error
    data2 = json.loads(res2.content[0].text)
    assert "Add team invitations" in data2["summary"]

    # 3. Test get_token_savings tool
    res3 = anyio.run(call_tool, "get_token_savings", {"request": "Add team invitations"})
    assert not res3.is_error
    data3 = json.loads(res3.content[0].text)
    assert "token savings" in data3["summary"]
    assert data3["data"]["files_avoided"] == 47
    assert data3["data"]["context_health"] == "Pristine"
