"""Phase 3 + 4 integration: audit the teamapp fixture into Neo4j, then exercise the knowledge lifecycle."""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_factory.auditor.constraints import check_cypher, cypher_params, evaluate
from agent_factory.auditor.graphview import GraphView
from agent_factory.auditor.pipeline import AuditOptions, run_audit
from agent_factory.common.embed import CachedEmbedder, HashEmbedder
from agent_factory.common.redact import contains_secret
from agent_factory.config import parse_config
from agent_factory.db.repos import AdminRepo, KnowledgeRepo
from agent_factory.memory.knowledge import KnowledgeService
from agent_factory.memory.validation import EvidenceRef, Proposal
from agent_factory.schema.uids import knowledge_uid
from tests.conftest import git
from tests.fixtures.build_teamapp import build_teamapp

from .conftest import TEST_DIMS

pytestmark = pytest.mark.neo4j


def config_for(pid: str):
    return parse_config(
        {
            "project": {"id": pid, "name": "TeamApp", "languages": ["typescript"]},
            "embeddings": {"provider": "hash", "model": "hash-bow-v1", "dimensions": TEST_DIMS},
        }
    )


def audit(root: Path, pid: str, stores, **opts):
    return run_audit(root, config_for(pid), stores, AuditOptions(**opts), embedder=HashEmbedder(TEST_DIMS))


@pytest.fixture(scope="module")
def audited(stores, tmp_path_factory):
    pid = f"ta{uuid.uuid4().hex[:8]}"
    root = build_teamapp(tmp_path_factory.mktemp("audit") / "teamapp")
    report = audit(root, pid, stores)
    yield root, pid, report
    AdminRepo(stores.domain, pid).purge_project()


@pytest.fixture
def own_copy(stores, tmp_path):
    pid = f"tc{uuid.uuid4().hex[:8]}"
    root = build_teamapp(tmp_path / "teamapp")
    yield root, pid
    AdminRepo(stores.domain, pid).purge_project()


def k(pid: str, kind: str, key: str) -> str:
    return knowledge_uid(pid, kind, key)


# -- Phase 3: the graph ----------------------------------------------------------------------------------------------


def test_full_audit_builds_expected_graph(stores, audited):
    _, pid, report = audited
    store = stores.domain
    assert report.mode == "full" and report.files["parsed"] == 28
    counts = {
        r["label"]: r["n"]
        for r in store.read(
            "MATCH (s:Symbol {project_id: $p}) UNWIND s.roles AS label RETURN label, count(*) AS n", p=pid
        )
    }
    assert counts == {
        "Service": 6,
        "Repository": 3,
        "Controller": 3,
        "Model": 4,
        "Route": 10,
        "Validator": 6,
        "Middleware": 2,
        "Integration": 1,
    }
    service_labels = store.read("MATCH (s:Symbol:Service {project_id: $p}) RETURN count(s) AS n", p=pid)
    assert service_labels[0]["n"] == 6
    paths = store.read(
        """MATCH p = (:Symbol:Route {project_id: $p, http_path: '/teams'})-[:HANDLES]->()<-[:HAS_MEMBER]-
                     (:Symbol:Controller)-[:USES]->(:Symbol:Service)-[:USES]->(:Symbol:Repository)
                     -[:HAS_MEMBER]->()-[:ACCESSES]->(m:Symbol:Model)
           RETURN DISTINCT m.name AS model""",
        p=pid,
    )
    assert {r["model"] for r in paths} >= {"teams", "teamMembers"}
    stats = AdminRepo(store, pid).stats()
    assert stats["labels"]["Commit"] == 25 and stats["labels"]["DocChunk"] == 7
    assert stats["relationships"]["CO_CHANGES"] >= 1 and stats["relationships"]["DESCRIBES"] >= 3
    assert (
        store.read(
            "MATCH (:Doc {project_id: $p})<-[:FROM_DOC]-(c:DocChunk) WHERE c.embedding IS NOT NULL "
            "RETURN count(c) AS n",
            p=pid,
        )[0]["n"]
        == 7
    )


def test_no_secret_reaches_the_graph(stores, audited):
    _, pid, _ = audited
    raw = stores.domain.read("MATCH (n {project_id: $p}) RETURN labels(n) AS labels, properties(n) AS props", p=pid)
    rows = [
        {"labels": r["labels"], "props": [(key, str(val)) for key, val in r["props"].items() if key != "embedding"]}
        for r in raw
    ]
    for row in rows:
        for key, value in row["props"]:
            assert not contains_secret(value or ""), (row["labels"], key, value)
    assert not stores.domain.read("MATCH (f:File {project_id: $p}) WHERE f.path ENDS WITH '.env' RETURN f", p=pid)
    assert not any("hunter22" in (v or "") for r in rows for _, v in r["props"])


def test_knowledge_from_the_audit(stores, audited):
    _, pid, _ = audited
    repo = KnowledgeRepo(stores.domain, pid)
    status = {x["uid"]: x for x in repo.list_knowledge(limit=1000)}
    for adr in ("ADR-001", "ADR-002", "ADR-004"):
        assert status[k(pid, "decision", adr)]["status"] == "validated"
    assert status[k(pid, "decision", "ADR-003")]["status"] == "superseded"
    assert stores.domain.read(
        "MATCH (:Decision {uid: $a})-[:SUPERSEDES]->(:Decision {uid: $b}) RETURN 1 AS ok",
        a=k(pid, "decision", "ADR-004"),
        b=k(pid, "decision", "ADR-003"),
    )
    forbid = status[k(pid, "constraint", "forbid-dependency-controller-repository")]
    assert forbid["status"] == "candidate" and forbid["violation_count"] == 1 and forbid["support_count"] == 2
    contra = [e for e in repo.evidence_of(forbid["uid"]) if e["rel"] == "CONTRADICTED_BY"]
    assert [e["path"] for e in contra] == ["src/controllers/user.controller.ts"]
    assert status[k(pid, "pattern", "naming-service")]["status"] == "validated"
    assert stores.domain.read(
        "MATCH (:Decision {uid: $d})-[:ESTABLISHES]->(c:Constraint {uid: $c}) RETURN 1 AS ok",
        d=k(pid, "decision", "ADR-002"),
        c=forbid["uid"],
    )
    follows = stores.domain.read(
        "MATCH (s:Symbol)-[:FOLLOWS]->(:Pattern {uid: $u}) RETURN s.name AS n",
        u=k(pid, "pattern", "lifecycle-token-tokenservice"),
    )
    assert {r["n"] for r in follows} == {"TokenService", "PasswordResetService", "EmailVerificationService"}
    scoped = stores.domain.read(
        "MATCH (s:Symbol:Controller)-[:CONSTRAINED_BY]->(:Constraint {uid: $u}) RETURN count(s) AS n", u=forbid["uid"]
    )
    assert scoped[0]["n"] == 3


def test_cypher_and_python_rule_evaluators_agree(stores, audited):
    _, pid, _ = audited
    view = GraphView.load(stores.domain, pid)
    for rule_type, rule in (
        ("forbid_dependency", {"from_role": "Controller", "to_roles": ["Repository", "Model"]}),
        ("restrict_access", {"allowed_roles": ["Repository"]}),
        ("restrict_access", {"allowed_roles": ["Service"]}),
    ):
        _, py_violations = evaluate(view, rule_type, rule)
        cypher = check_cypher(rule_type, rule)
        assert (
            cypher is not None
            and stores.domain.explain_query_type(cypher, p=pid, **cypher_params(rule_type, rule)) == "r"
        )
        rows = stores.domain.read(cypher, p=pid, **cypher_params(rule_type, rule))
        assert {r["violator"] for r in rows} == {v for v, _ in py_violations}, rule


def test_unchanged_reaudit_is_incremental_and_changes_nothing(stores, audited):
    root, pid, _ = audited
    before = AdminRepo(stores.domain, pid).stats()
    knowledge_before = {
        x["uid"]: (x["status"], x["confidence"]) for x in KnowledgeRepo(stores.domain, pid).list_knowledge()
    }
    report = audit(root, pid, stores)
    after = AdminRepo(stores.domain, pid).stats()
    assert report.mode == "incremental" and report.files["written"] == 0
    before["labels"]["AuditRun"] += 1
    assert after == before
    assert {
        x["uid"]: (x["status"], x["confidence"]) for x in KnowledgeRepo(stores.domain, pid).list_knowledge()
    } == knowledge_before


def test_incremental_modify_add_delete(stores, own_copy):
    root, pid = own_copy
    first = audit(root, pid, stores)
    store = stores.domain
    token_run = store.read(
        "MATCH (f:File {project_id: $p, path: 'src/services/token.service.ts'}) RETURN f.last_audit_run AS r", p=pid
    )[0]["r"]
    team = root / "src/services/team.service.ts"
    team.write_text(
        team.read_text(encoding="utf-8").replace(
            "  async listMembers(", "  async removeMember(teamId: string) { return teamId; }\n\n  async listMembers("
        ),
        encoding="utf-8",
    )
    (root / "src/services/audit-log.service.ts").write_text(
        "import { TeamService } from './team.service';\nexport class AuditLogService {\n"
        "  constructor(private teams: TeamService) {}\n  log() { return this.teams.listMembers('x'); }\n}\n",
        encoding="utf-8",
    )
    (root / "src/controllers/user.controller.ts").unlink()
    second = audit(root, pid, stores)
    assert first.mode == "full" and second.mode == "incremental"
    assert second.files["changed"] == 2 and second.files["deleted"] == 1
    names = {r["n"] for r in store.read("MATCH (s:Symbol {project_id: $p}) RETURN s.qualname AS n", p=pid)}
    assert "TeamService.removeMember" in names and "AuditLogService" in names and "UserController" not in names
    assert (
        store.read(
            "MATCH (:Symbol {qualname: 'AuditLogService', project_id: $p})-[:USES]->(t:Symbol) RETURN t.name AS n",
            p=pid,
        )[0]["n"]
        == "TeamService"
    )
    assert (
        store.read(
            "MATCH (f:File {project_id: $p, path: 'src/services/token.service.ts'}) RETURN f.last_audit_run AS r", p=pid
        )[0]["r"]
        == token_run
    )  # untouched file not rewritten
    forbid = KnowledgeRepo(store, pid).get(k(pid, "constraint", "forbid-dependency-controller-repository"))
    assert forbid is not None and forbid["violation_count"] == 0


def test_revalidation_deprecates_and_restores_human_approved_pattern(stores, own_copy):
    root, pid = own_copy
    audit(root, pid, stores)
    service = KnowledgeService(stores.domain, pid, root)
    uid = k(pid, "pattern", "lifecycle-token-tokenservice")
    service.approve(uid, "human:reviewer", "TokenService is the token store")
    for rel in ("src/services/password-reset.service.ts", "src/services/email-verification.service.ts"):
        (root / rel).unlink()
    audit(root, pid, stores)
    item = service.repo.get(uid)
    assert item is not None and item["status"] == "deprecated" and item["support_count"] == 1
    git(root, "checkout", "--", "src/services")
    audit(root, pid, stores)
    item = service.repo.get(uid)
    assert item is not None and item["status"] == "validated" and "re-observed" in item["status_reason"]
    actions = [(e["actor"], e.get("to_status")) for e in reversed(service.repo.events(uid))]
    assert actions == [
        ("auditor", "candidate"),
        ("human:reviewer", "validated"),
        ("auditor", "deprecated"),
        ("auditor", "validated"),
    ]


def test_agent_proposals_merge_rederive_and_never_self_validate(stores, audited):
    root, pid, _ = audited
    service = KnowledgeService(stores.domain, pid, root)
    rule = {"from_role": "Controller", "to_roles": ["Repository", "Model"]}
    merged = service.submit(
        Proposal(
            kind="constraint",
            title="No repositories in controllers",
            claim="Use services.",
            source="agent",
            rule_type="forbid_dependency",
            rule=rule,
            evidence=[EvidenceRef(path="src/controllers/team.controller.ts")],
        ),
        "agent:claude",
    )
    assert merged.outcome == "merged" and merged.uid == k(pid, "constraint", "forbid-dependency-controller-repository")
    fresh = service.submit(
        Proposal(
            kind="pattern",
            title="Handlers return JSON",
            claim="Controllers respond with JSON",
            source="agent",
            evidence=[
                EvidenceRef.parse(p)
                for p in (
                    "src/controllers/team.controller.ts:14-16",
                    "src/controllers/user.controller.ts:13-15",
                    "src/controllers/auth.controller.ts:15-19",
                )
            ],
        ),
        "agent:claude",
    )
    assert fresh.outcome == "created" and fresh.status == "candidate"
    service.reject(fresh.uid, "human:reviewer", "not a real convention")  # type: ignore[arg-type]
    again = service.submit(
        Proposal(
            kind="pattern",
            title="Handlers return JSON!",
            claim="again",
            source="agent",
            evidence=[EvidenceRef(path="src/index.ts")],
        ),
        "agent:claude",
    )
    assert again.outcome == "rejected" and "rejected before" in again.reasons[0]
    bad = service.submit(
        Proposal(
            kind="pattern",
            title="Nothing here",
            claim="Nothing to see",
            source="agent",
            evidence=[EvidenceRef(path="src/nope.ts")],
        ),
        "agent:claude",
    )
    assert bad.outcome == "rejected" and bad.reasons == ["no valid evidence"]


def test_every_change_is_in_graph_and_on_disk(stores, own_copy):
    root, pid = own_copy
    audit(root, pid, stores)
    service = KnowledgeService(stores.domain, pid, root)
    service.approve(k(pid, "pattern", "errors-extend-base"), "human:r")
    lines = (root / ".agent-factory/audit.log").read_text(encoding="utf-8").strip().splitlines()
    events = service.repo.events(limit=10_000)
    assert len(lines) == len(events) and {json.loads(line)["uid"] for line in lines} == {e["uid"] for e in events}


def test_delete_export_import_and_purge(stores, own_copy):
    root, pid = own_copy
    audit(root, pid, stores, history=False)
    service = KnowledgeService(stores.domain, pid, root)
    admin = AdminRepo(stores.domain, pid)
    labels = ["Knowledge", "Evidence", "MemoryEvent"]
    before = admin.export(labels=labels)
    uid = k(pid, "pattern", "errors-extend-base")
    service.delete(uid, "human:r")
    assert service.repo.get(uid) is None
    assert not stores.domain.read("MATCH (e:Evidence {project_id: $p}) WHERE NOT (e)<--() RETURN e LIMIT 1", p=pid)
    assert service.repo.events(uid)[0]["action"] == "delete"
    for node in before["nodes"]:
        stores.domain.write("MATCH (n {uid: $u}) DETACH DELETE n", u=node["props"]["uid"])
    admin.import_(before)
    after = admin.export(labels=labels)
    after_nodes = {n["props"]["uid"]: n for n in after["nodes"]}
    assert all(after_nodes.get(n["props"]["uid"]) == n for n in before["nodes"])
    before_rels = {(r["src"], r["type"], r["dst"]) for r in before["relationships"]}
    assert before_rels <= {(r["src"], r["type"], r["dst"]) for r in after["relationships"]}


def test_embedding_cache_is_reused_across_audits(stores, own_copy, tmp_path):
    root, pid = own_copy

    class Counting(HashEmbedder):
        calls = 0

        def embed(self, texts):
            Counting.calls += len(texts)
            return super().embed(texts)

    embedder = CachedEmbedder(Counting(TEST_DIMS), tmp_path / "cache.sqlite")
    run_audit(root, config_for(pid), stores, AuditOptions(full=True, history=False), embedder=embedder)
    first = Counting.calls
    AdminRepo(stores.domain, pid).purge_project()
    run_audit(root, config_for(pid), stores, AuditOptions(full=True, history=False), embedder=embedder)
    assert first > 0 and Counting.calls == first
    embedder.close()


def test_cli_audit_and_memory_flow(stores, neo4j_settings, own_copy, monkeypatch):
    from agent_factory.cli.main import app

    root, pid = own_copy
    (root / "agent-factory.yaml").write_text(
        json.dumps(
            {
                "project": {"id": pid, "name": "TeamApp", "languages": ["typescript"]},
                "embeddings": {"provider": "hash", "model": "hash-bow-v1", "dimensions": TEST_DIMS},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEO4J_URI", neo4j_settings.uri)
    monkeypatch.setenv("NEO4J_USERNAME", neo4j_settings.username)
    monkeypatch.setenv("NEO4J_PASSWORD", neo4j_settings.password)
    monkeypatch.delenv("NEO4J_DATABASE", raising=False)
    monkeypatch.delenv("MVP_NEO4J_URI", raising=False)
    runner = CliRunner()
    base = ["--repo", str(root), "--json"]
    result = runner.invoke(app, [*base, "audit", "--no-history"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["knowledge"]["created"] > 20
    listed = json.loads(runner.invoke(app, [*base, "memory", "list", "--status", "candidate"]).stdout)
    assert any(x["uid"].endswith("lifecycle-token-tokenservice") for x in listed)
    approved = runner.invoke(app, [*base, "memory", "approve", "lifecycle-token-tokenservice", "--reason", "ok"])
    assert approved.exit_code == 0, approved.output and json.loads(approved.stdout)["status"] == "validated"
    shown = json.loads(runner.invoke(app, [*base, "memory", "show", "lifecycle-token-tokenservice"]).stdout)
    assert shown["knowledge"]["status"] == "validated" and len(shown["evidence"]) == 3
    bad = runner.invoke(
        app,
        [
            *base,
            "memory",
            "propose",
            "--kind",
            "pattern",
            "--title",
            "Leak",
            "--claim",
            "key AKIAIOSFODNN7EXAMPLE",
            "--evidence",
            "src/index.ts",
        ],
    )
    assert bad.exit_code == 1
    status = json.loads(runner.invoke(app, [*base, "status"]).stdout)
    assert status["last_audit"]["mode"] == "full" and status["memory"]["pattern"]["validated"] >= 2
    shutil.rmtree(root / ".agent-factory", ignore_errors=True)


def test_agent_memory_roundtrip(neo4j_settings):
    from agent_factory.memory.agent_memory import AgentMemory

    async def run():
        mem = AgentMemory(neo4j_settings, None, HashEmbedder(1536))
        await mem.open()
        try:
            session = f"feature:test-{uuid.uuid4().hex[:6]}"
            await mem.add_message(session, "user", "Add team invitations that reuse the token service")
            trace = await mem.start_trace(session, "Add team invitations")
            await mem.add_step(
                trace,
                "Check reuse",
                "find_reusable",
                "find_reusable",
                {"name": "InvitationTokenService"},
                "TokenService",
            )
            await mem.complete_trace(trace, "reused TokenService", True)
            await mem.save_fact("TeamInvitation", "uses", "TokenService")
            similar = await mem.similar_traces("team invitations feature")
            context = await mem.context("team invitations", session)
        finally:
            await mem.close()
        return similar, context

    similar, context = asyncio.run(run())
    assert any(t.task == "Add team invitations" for t in similar)
    assert "team invitations" in context.lower()
