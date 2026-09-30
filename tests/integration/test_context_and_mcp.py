"""Phase 5 + 6 integration: context engine, reuse, impact, eval gates, and the MCP server contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import anyio
import pytest
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from agent_factory.auditor.pipeline import AuditOptions, run_audit
from agent_factory.common.embed import HashEmbedder
from agent_factory.config import parse_config
from agent_factory.context.impact import analyze
from agent_factory.context.reuse import ProposedAbstraction
from agent_factory.db.repos import AdminRepo
from agent_factory.db.stores import Neo4jStore
from agent_factory.eval import load_cases, run_ablation, score_reuse
from agent_factory.mcp.server import build_server
from agent_factory.memory.agent_memory import TraceSummary
from agent_factory.runtime import Runtime
from tests.conftest import git
from tests.fakes import FakeAgentMemory
from tests.fixtures.build_teamapp import build_teamapp

from .conftest import TEST_DIMS

pytestmark = pytest.mark.neo4j
EVAL = Path(__file__).resolve().parents[1] / "eval"


def _config(pid: str):
    return parse_config(
        {
            "project": {"id": pid, "name": "TeamApp", "languages": ["typescript"]},
            "embeddings": {"provider": "hash", "model": "hash-bow-v2", "dimensions": TEST_DIMS},
        }
    )


@pytest.fixture(scope="module")
def project(stores, tmp_path_factory):
    pid = f"tx{uuid.uuid4().hex[:8]}"
    root = build_teamapp(tmp_path_factory.mktemp("ctx") / "teamapp")
    (root / "agent-factory.yaml").write_text(
        json.dumps(
            {
                "project": {"id": pid, "name": "TeamApp", "languages": ["typescript"]},
                "embeddings": {"provider": "hash", "model": "hash-bow-v2", "dimensions": TEST_DIMS},
            }
        ),
        encoding="utf-8",
    )
    run_audit(root, _config(pid), stores, AuditOptions(), embedder=HashEmbedder(TEST_DIMS))
    yield root, pid
    AdminRepo(stores.domain, pid).purge_project()


@pytest.fixture
def runtime(stores, project):
    root, pid = project
    memory = FakeAgentMemory(
        traces=[TraceSummary("Add password reset", "reused TokenService", True, [])],
        preferences=["Validate route input with Zod schemas"],
    )
    return Runtime(root, _config(pid), stores, env={}, embedder=HashEmbedder(TEST_DIMS), memory=memory)


# -- Phase 5 ----------------------------------------------------------------------------------------------------------


def test_reuse_invitation_token_service(runtime):
    report = runtime.reuse().find(
        ProposedAbstraction(
            name="InvitationTokenService",
            description="Create, validate and expire invitation tokens",
            methods=["create", "validate", "expire"],
        )
    )
    top = report.top
    assert top is not None and top.name == "TokenService" and top.verdict == "reuse"
    assert "consume" in (top.lifecycle or "") and set(top.used_by) == {
        "PasswordResetService",
        "EmailVerificationService",
    }


def test_reuse_new_ok_for_unrelated(runtime):
    report = runtime.reuse().find(
        ProposedAbstraction(name="CsvExportService", description="Export rows as CSV", methods=["exportCsv"])
    )
    assert report.verdict == "new_ok"


def test_impact_token_service(runtime):
    report = analyze(runtime.store, runtime.project_id, "TokenService", depth=2)
    names = {d.name: d.hops for d in report.dependents}
    assert (
        names["PasswordResetService"] == 1 and names["EmailVerificationService"] == 1 and names["AuthController"] == 2
    )
    assert "POST /auth/password-reset" in report.routes and "tests/token.service.test.ts" in report.tests
    assert {d.name for d in report.dependencies} >= {"TokenRepository"}


def test_context_pack_for_team_invitations(runtime):
    pack = runtime.engine().build("Add team invitations", budget=4000)
    names = {s.name for s in pack.reusable}
    assert {"TeamService", "TokenService", "EmailService"} <= names
    assert any("ADR-002" in d.title for d in pack.decisions)
    assert all(k.status == "validated" for k in pack.constraints + pack.patterns + pack.decisions)
    assert all(k.status == "candidate" for k in pack.unverified)
    assert any(k.title.startswith("Controllers must not use repositories") for k in pack.unverified)
    assert pack.budget.used <= 4000
    assert pack.memory.preferences == ["Validate route input with Zod schemas"]
    assert pack.memory.similar_tasks[0].outcome == "reused TokenService"
    assert any("TeamService" in a.text for a in pack.architecture)


def test_superseded_adr_only_with_history(runtime):
    now = runtime.engine().build("Store tokens in Redis again")
    assert not any("ADR-003" in d.title for d in now.decisions)
    assert any("ADR-004" in d.title for d in now.decisions)
    history = runtime.engine().build("Store tokens in Redis again", include_history=True)
    assert any("ADR-003" in d.title for d in history.decisions)


def test_small_budget_keeps_constraints(runtime):
    pack = runtime.engine().build("Add team invitations", budget=300)
    assert pack.budget.cut and len(pack.constraints) >= 1


@pytest.mark.eval
def test_eval_gates(runtime):
    overall, splits = run_ablation(runtime.engine(), load_cases(EVAL / "retrieval_cases.yaml"))
    best = overall[-1]
    assert best.recall_at_k >= 0.80, best.per_case
    assert best.mrr >= 0.60
    no_graph = overall[-2]
    assert best.mrr > no_graph.mrr  # graph expansion improves ranking
    assert splits["holdout"][-1].recall_at_k >= 0.75
    reuse = score_reuse(runtime.reuse(), load_cases(EVAL / "reuse_cases.yaml"))
    assert reuse.precision >= 0.80 and reuse.recall >= 0.80, reuse.per_case


# -- Phase 6: MCP contract --------------------------------------------------------------------------------------------

READ_TOOLS = {
    "get_feature_context",
    "get_memory_context",
    "find_reusable",
    "impact_of",
    "get_constraints",
    "get_patterns",
    "search_memory",
    "ask_graph",
    "how_did_we_handle",
    "how_did_i_handle",
    "get_feature",
    "check_changes",
    "get_token_savings",
}


def call(server, tool: str, args: dict | None = None):
    async def run():
        async with Client(server) as c:
            return await c.call_tool(tool, args or {})

    res = anyio.run(run)
    text = res.content[0].text if res.content else ""
    return res.is_error, (json.loads(text) if not res.is_error else text)


@pytest.fixture
def server(runtime):
    srv = build_server(lambda: runtime)
    yield srv


def test_tool_catalogue_snapshot(server, snapshot):
    async def run():
        async with Client(server) as c:
            return await c.list_tools()

    tools = anyio.run(run).tools
    catalogue = {
        t.name: {"input": t.input_schema, "read_only": bool(t.annotations and t.annotations.read_only_hint)}
        for t in tools
    }
    assert {n for n, t in catalogue.items() if t["read_only"]} == READ_TOOLS
    assert catalogue == snapshot


@pytest.mark.parametrize(
    "tool,args,check",
    [
        ("get_feature_context", {"request": "Add team invitations"}, lambda d: "TokenService" in d["summary"]),
        (
            "find_reusable",
            {
                "name": "InvitationTokenService",
                "methods": ["create", "validate", "expire"],
                "description": "invitation tokens",
            },
            lambda d: d["data"]["verdict"] == "reuse",
        ),
        ("impact_of", {"symbol_or_path": "TokenService"}, lambda d: "PasswordResetService" in d["summary"]),
        ("get_constraints", {}, lambda d: len(d["data"]["constraints"]) >= 1),
        ("get_constraints", {"scope": "Controller"}, lambda d: isinstance(d["data"]["constraints"], list)),
        ("get_patterns", {"include_candidates": True}, lambda d: len(d["data"]["patterns"]) >= 3),
        (
            "search_memory",
            {"query": "token lifecycle", "include_candidates": True},
            lambda d: any("Token" in k["title"] for k in d["data"]["knowledge"]),
        ),
        ("how_did_we_handle", {"task": "password reset"}, lambda d: d["data"]["traces"][0]["success"] is True),
    ],
)
def test_read_tools(server, tool, args, check):
    is_error, data = call(server, tool, args)
    assert not is_error, data
    assert check(data)


def test_read_tools_never_write(server, monkeypatch):
    def refuse(self, *a, **k):
        raise AssertionError("a read tool wrote to the graph")

    monkeypatch.setattr(Neo4jStore, "write", refuse)
    monkeypatch.setattr(Neo4jStore, "write_batches", refuse)
    for tool, args in [
        ("get_feature_context", {"request": "team"}),
        ("find_reusable", {"name": "XService"}),
        ("impact_of", {"symbol_or_path": "TeamService"}),
        ("get_constraints", {}),
        ("get_patterns", {}),
        ("search_memory", {"query": "team"}),
    ]:
        is_error, data = call(server, tool, args)
        assert not is_error, (tool, data)


def test_errors_are_actionable(server, stores, tmp_path):
    is_error, text = call(server, "impact_of", {"symbol_or_path": "NoSuchThing"})
    assert is_error and "Use a class/function name" in text
    is_error, text = call(server, "find_reusable", {"name": "X"})
    assert is_error and "invalid proposal: name" in text
    empty = Runtime(
        tmp_path, _config(f"none{uuid.uuid4().hex[:6]}"), stores, env={}, embedder=False, memory=FakeAgentMemory()
    )
    is_error, text = call(build_server(lambda: empty), "get_feature_context", {"request": "x"})
    assert is_error and "Run: agent-factory audit" in text


def test_propose_memory_cannot_validate_and_rejects_secrets(server):
    evidence = [f"src/services/{f}.service.ts" for f in ("team", "token", "email", "user", "password-reset")]
    is_error, data = call(
        server,
        "propose_memory",
        {
            "kind": "pattern",
            "title": "Services end with Service",
            "claim": "Every service class name ends in Service",
            "evidence": evidence,
        },
    )
    assert not is_error and data["data"]["status"] == "candidate"
    is_error, data = call(
        server,
        "propose_memory",
        {"kind": "pattern", "title": "Leaky", "claim": "our key is AKIAIOSFODNN7EXAMPLE", "evidence": ["src/index.ts"]},
    )
    assert not is_error and data["data"]["outcome"] == "rejected"


def test_feature_session_traces_tool_calls(server, runtime, project):
    root, _ = project
    git(root, "checkout", "-q", "-B", f"feat/invites-{uuid.uuid4().hex[:4]}")
    try:
        is_error, started = call(
            server, "start_feature", {"name": "Team invitations", "request": "Add team invitations"}
        )
        assert not is_error
        fid = started["data"]["feature"]["feature_id"]
        call(server, "find_reusable", {"name": "InvitationTokenService", "description": "password: 'hunter2secret'"})
        is_error, planned = call(
            server,
            "record_plan",
            {
                "plan": {
                    "summary": "Reuse TokenService",
                    "planned_files": ["src/services/team.service.ts"],
                    "reuse_decisions": [
                        {"proposed": "InvitationTokenService", "verdict": "reuse", "chosen": "TokenService"}
                    ],
                }
            },
        )
        assert not is_error and planned["data"]["status"] == "in_progress"
        is_error, feature = call(server, "get_feature", {})
        assert not is_error and feature["data"]["feature_id"] == fid and feature["data"]["steps"] >= 2
        steps = runtime.memory.steps()
        reuse_step = next(s for s in steps if s["tool_name"] == "find_reusable")
        assert "hunter2secret" not in json.dumps(reuse_step)
        assert (
            (root / ".agent-factory/features" / fid / "plan.md")
            .read_text(encoding="utf-8")
            .startswith("# FEATURE PLAN")
        )
        assert (
            runtime.store.read("MATCH (f:Feature {uid: $u}) RETURN f.status AS s", u=started["data"]["feature"]["uid"])[
                0
            ]["s"]
            == "in_progress"
        )
    finally:
        git(root, "checkout", "-q", "main")
    is_error, feature = call(server, "get_feature", {})
    assert feature["data"] is None  # another branch: no active feature


def test_ask_graph_is_guarded(server, monkeypatch):
    import agent_factory.context.ask as ask_mod

    monkeypatch.setattr(
        ask_mod,
        "graphrag_generator",
        lambda *a, **k: (
            lambda q: (
                "MATCH (r:Symbol:Route {project_id: $project_id}) WHERE r.validated = false "
                "RETURN r.http_method + ' ' + r.http_path AS route"
            )
        ),
    )
    is_error, data = call(server, "ask_graph", {"question": "Which routes have no validation?"})
    assert not is_error and {"route": "PATCH /me"} in data["data"]["rows"]
    monkeypatch.setattr(ask_mod, "graphrag_generator", lambda *a, **k: lambda q: "MATCH (n) DETACH DELETE n")
    is_error, text = call(server, "ask_graph", {"question": "delete everything"})
    assert is_error and "refused" in text


def test_phase9_and_10_tools_are_active(server):
    is_error, data = call(server, "check_changes", {})
    assert not is_error and "summary" in data and "data" in data

    is_error, text = call(server, "add_evidence", {"kind": "test_run", "path": "x", "summary": "y"})
    assert is_error and "no active feature" in text

    is_error, text = call(server, "complete_feature", {})
    assert is_error and "no active feature" in text


@pytest.mark.e2e
def test_stdio_server_smoke(project, neo4j_settings):
    root, _ = project
    env = {
        **os.environ,
        "NEO4J_URI": neo4j_settings.uri,
        "NEO4J_USERNAME": neo4j_settings.username,
        "NEO4J_PASSWORD": neo4j_settings.password,
        "MVP_NEO4J_URI": "",
        "OPENAI_API_KEY": "",
    }
    env.pop("NEO4J_DATABASE", None)
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent_factory", "mcp", "serve"], cwd=str(root), env=env
    )

    async def run():
        async with Client(params) as c:
            tools = await c.list_tools()
            res = await c.call_tool(
                "find_reusable", {"name": "InvitationTokenService", "methods": ["create", "validate", "expire"]}
            )
            return [t.name for t in tools.tools], json.loads(res.content[0].text)

    names, data = anyio.run(run)
    assert "get_feature_context" in names and data["data"]["candidates"][0]["name"] == "TokenService"
    assert subprocess.run(
        [sys.executable, "-m", "agent_factory", "--version"], capture_output=True, text=True, check=True
    ).stdout.startswith("agent-factory")
