"""Phase 2 integration: repository layer."""

from __future__ import annotations

import math

import pytest

from agent_factory.db.repos import AdminRepo, CodeRepo, FeatureRepo, HistoryRepo, KnowledgeRepo
from agent_factory.schema.model import Label, Rel
from agent_factory.schema.uids import feature_uid, file_uid, knowledge_uid, module_uid, symbol_uid

from .conftest import TEST_DIMS

pytestmark = pytest.mark.neo4j
RUN = "run-1"


def _seed(repo: CodeRepo, pid: str, run: str = RUN, n: int = 3) -> list[str]:
    repo.upsert_project("Shop", ["typescript"])
    repo.upsert_modules([{"uid": module_uid(pid, "src"), "path": "src", "kind": "dir", "parent_uid": None}], run)
    files = [
        {
            "uid": file_uid(pid, f"src/s{i}.ts"),
            "path": f"src/s{i}.ts",
            "lang": "typescript",
            "sha256": f"h{i}",
            "loc": 10,
            "module_uid": module_uid(pid, "src"),
            "is_test": False,
        }
        for i in range(n)
    ]
    repo.upsert_files(files, run)
    symbols = [
        {
            "uid": symbol_uid(pid, f"src/s{i}.ts", f"S{i}Service"),
            "file_uid": file_uid(pid, f"src/s{i}.ts"),
            "props": {"name": f"S{i}Service", "kind": "class", "path": f"src/s{i}.ts", "name_tokens": f"s{i} service"},
            "roles": ["Service"],
            "role_confidence": 0.9,
        }
        for i in range(n)
    ]
    repo.upsert_symbols(symbols, run)
    return [s["uid"] for s in symbols]


def test_upsert_symbols_idempotent_and_role_labels(store, project_id):
    repo = CodeRepo(store, project_id)
    uids = _seed(repo, project_id)
    _seed(repo, project_id)
    assert len(repo.symbols_by_role("Service")) == 3
    sym = repo.get_symbol(uids[0])
    assert sym is not None and "Service" in sym["labels"] and sym["project_id"] == project_id
    # A role change replaces the label instead of accumulating.
    repo.upsert_symbols(
        [
            {
                "uid": uids[0],
                "file_uid": file_uid(project_id, "src/s0.ts"),
                "props": {"name": "S0Service"},
                "roles": ["Repository"],
                "role_confidence": 0.7,
            }
        ],
        RUN,
    )
    sym = repo.get_symbol(uids[0])
    assert sym is not None and "Repository" in sym["labels"] and "Service" not in sym["labels"]


def test_edges_whitelist_and_stale_edge_cleanup(store, project_id):
    repo = CodeRepo(store, project_id)
    uids = _seed(repo, project_id)
    repo.upsert_edges(Rel.USES, [{"src": uids[0], "dst": uids[1]}, {"src": uids[0], "dst": uids[2]}], RUN)
    with pytest.raises(ValueError):
        repo.upsert_edges("X]->() DETACH DELETE n //", [{"src": uids[0], "dst": uids[1]}], RUN)
    # Next run re-audits uids[0] and only keeps one edge.
    repo.upsert_edges(Rel.USES, [{"src": uids[0], "dst": uids[1]}], "run-2")
    assert repo.delete_stale_edges([uids[0]], "run-2") >= 1
    rows = store.read("MATCH (a:Symbol {uid: $u})-[:USES]->(b) RETURN b.uid AS b", u=uids[0])
    assert [r["b"] for r in rows] == [uids[1]]


def test_delete_file_subgraph_keeps_knowledge_and_marks_evidence_stale(store, project_id):
    repo = CodeRepo(store, project_id)
    uids = _seed(repo, project_id)
    kr = KnowledgeRepo(store, project_id)
    kuid = knowledge_uid(project_id, "pattern", "services")
    kr.upsert({"uid": kuid, "kind": "pattern", "title": "Services", "claim": "c", "status": "candidate"})
    kr.attach_evidence(kuid, [{"path": "src/s0.ts", "symbol_uid": uids[0], "line_start": 1, "line_end": 3}])
    assert repo.delete_file_subgraph([file_uid(project_id, "src/s0.ts")]) == 1
    assert repo.get_symbol(uids[0]) is None
    assert kr.get(kuid) is not None
    ev = kr.evidence_of(kuid)
    assert len(ev) == 1 and ev[0]["stale"] is True


def test_vector_index_query_roundtrip(store, project_id):
    repo = CodeRepo(store, project_id)
    uids = _seed(repo, project_id)

    def vec(i: int) -> list[float]:
        v = [0.0] * TEST_DIMS
        v[i] = 1.0
        return v

    repo.set_embeddings(Label.SYMBOL, [{"uid": u, "embedding": vec(i)} for i, u in enumerate(uids)])
    q = vec(1)
    q[2] = 0.1
    norm = math.sqrt(sum(x * x for x in q))
    rows = store.read(
        "CALL db.index.vector.queryNodes('af_symbol_embedding', 10, $q) YIELD node, score "
        "WHERE node.project_id = $p RETURN node.uid AS uid",
        q=[x / norm for x in q],
        p=project_id,
    )
    assert rows[0]["uid"] == uids[1]


def test_fulltext_camelcase_tokens(store, project_id):
    repo = CodeRepo(store, project_id)
    _seed(repo, project_id)
    repo.upsert_files(
        [
            {
                "uid": file_uid(project_id, "src/v.ts"),
                "path": "src/v.ts",
                "lang": "typescript",
                "sha256": "x",
                "loc": 1,
                "module_uid": None,
                "is_test": False,
            }
        ],
        RUN,
    )
    repo.upsert_symbols(
        [
            {
                "uid": symbol_uid(project_id, "src/v.ts", "VerificationTokenService"),
                "file_uid": file_uid(project_id, "src/v.ts"),
                "props": {"name": "VerificationTokenService", "name_tokens": "verification token service"},
                "roles": ["Service"],
                "role_confidence": 0.9,
            }
        ],
        RUN,
    )
    rows = store.read(
        "CALL db.index.fulltext.queryNodes('af_symbol_text', 'token') YIELD node "
        "WHERE node.project_id = $p RETURN node.name AS name",
        p=project_id,
    )
    assert [r["name"] for r in rows] == ["VerificationTokenService"]


def test_project_isolation(store, project_id):
    other = f"{project_id}x"
    try:
        _seed(CodeRepo(store, project_id), project_id)
        _seed(CodeRepo(store, other), other, n=1)
        assert len(CodeRepo(store, project_id).symbols_by_role("Service")) == 3
        assert len(CodeRepo(store, other).symbols_by_role("Service")) == 1
        AdminRepo(store, other).purge_project()
        assert len(CodeRepo(store, project_id).symbols_by_role("Service")) == 3
    finally:
        AdminRepo(store, other).purge_project()


def test_knowledge_lifecycle_storage_and_events(store, project_id):
    kr = KnowledgeRepo(store, project_id)
    kuid = knowledge_uid(project_id, "constraint", "no-db-in-controllers")
    kr.upsert(
        {
            "uid": kuid,
            "kind": "constraint",
            "title": "No DB in controllers",
            "claim": "c",
            "status": "candidate",
            "confidence": 0.5,
        }
    )
    assert kr.set_status(kuid, "validated", confidence=0.9) == "candidate"
    kr.record_event(
        {
            "uid": "e1-" + project_id,
            "target_uid": kuid,
            "action": "validate",
            "from_status": "candidate",
            "to_status": "validated",
            "actor": "human:test",
            "at": "2026-01-01",
        }
    )
    assert kr.get(kuid)["status"] == "validated"  # type: ignore[index]
    assert kr.counts() == {"constraint": {"validated": 1}}
    assert kr.events(kuid)[0]["to_status"] == "validated"
    assert kr.list_knowledge(status="validated")[0]["uid"] == kuid
    with pytest.raises(KeyError):
        kr.set_status("nope", "validated")


def test_feature_links(store, project_id):
    repo = CodeRepo(store, project_id)
    uids = _seed(repo, project_id)
    fr = FeatureRepo(store, project_id)
    fuid = feature_uid(project_id, "feat_20260926_invites")
    fr.create(fuid, "Invites", "Add team invitations", branch="feat/invites")
    assert fr.link(fuid, Rel.REUSES, [uids[0]]) == 1
    with pytest.raises(ValueError):
        fr.link(fuid, Rel.CALLS, [uids[1]])
    fr.update(fuid, status="in_progress")
    assert fr.get(fuid)["status"] == "in_progress"  # type: ignore[index]
    assert fr.links(fuid) == {"REUSES": [uids[0]]}


def test_co_changes(store, project_id):
    repo = CodeRepo(store, project_id)
    _seed(repo, project_id)
    hr = HistoryRepo(store, project_id)
    commits = [
        {
            "uid": f"{project_id}:commit:c{i}",
            "sha": f"c{i}",
            "message": "m",
            "type": "feat",
            "author_hash": "a",
            "date": "2026",
            "file_count": 2,
        }
        for i in range(3)
    ]
    hr.upsert_commits(commits)
    touches = [
        {"commit_uid": c["uid"], "file_uid": file_uid(project_id, f), "added": 1, "deleted": 0}
        for c in commits
        for f in ("src/s0.ts", "src/s1.ts")
    ]
    hr.upsert_touches(touches)
    assert hr.recompute_co_changes(min_count=3) == 1
    assert hr.recompute_co_changes(min_count=4) == 0


def test_export_import_roundtrip(store, project_id):
    repo = CodeRepo(store, project_id)
    _seed(repo, project_id)
    admin = AdminRepo(store, project_id)
    before = admin.export()
    admin.purge_project()
    assert admin.stats()["labels"] == {}
    store.write("MERGE (p:Project {id: $id})", id=project_id)
    admin.import_(before)
    after = admin.export()
    assert after["nodes"] == before["nodes"]
    assert after["relationships"] == before["relationships"]
