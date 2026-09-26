"""Phase 1 + 2 integration: stores, migrations, schema."""

from __future__ import annotations

import pytest
from neo4j.exceptions import ClientError, ConstraintError

from agent_factory.db import GraphStores
from agent_factory.db.migrations import (
    apply_migrations,
    constraint_names,
    current_version,
    index_states,
    latest_version,
    vector_dimension_mismatches,
)
from agent_factory.schema.doc import schema_markdown

from .conftest import TEST_DIMS, drop_schema

pytestmark = pytest.mark.neo4j


def test_read_rejects_write(store, project_id):
    with pytest.raises(ClientError):
        store.read("CREATE (n:Scratch {project_id: $p})", p=project_id)
    assert store.read("MATCH (n:Scratch {project_id: $p}) RETURN count(n) AS n", p=project_id)[0]["n"] == 0


def test_explain_query_type(store):
    assert store.explain_query_type("MATCH (n:Symbol) RETURN n LIMIT 1") == "r"
    assert store.explain_query_type("MATCH (n:Symbol) SET n.x = 1") in ("w", "rw")


def test_same_instance_mode_shares_driver(neo4j_settings):
    s = GraphStores.from_settings(neo4j_settings, neo4j_settings)
    try:
        assert s.same_instance and s.memory is not None and s.memory.driver is s.domain.driver
    finally:
        s.close()


def test_write_batches_counts_rows(store, project_id):
    rows = [{"i": i} for i in range(2500)]
    n = store.write_batches(
        "UNWIND $rows AS row CREATE (:Scratch {project_id: $p, i: row.i})", rows, size=1000, p=project_id
    )
    assert n == 2500
    assert store.read("MATCH (n:Scratch {project_id: $p}) RETURN count(n) AS n", p=project_id)[0]["n"] == 2500
    store.write("MATCH (n:Scratch {project_id: $p}) DETACH DELETE n", p=project_id)


def test_server_supports_vector_index(store):
    assert store.server_info().supports_vector_index()


def test_migrations_apply_on_empty_db_and_are_idempotent(store):
    drop_schema(store)
    assert current_version(store) == 0
    first = apply_migrations(store, TEST_DIMS)
    assert first.from_version == 0 and first.to_version == latest_version(TEST_DIMS)
    before = (constraint_names(store), [(s.name, s.type) for s in index_states(store)])
    second = apply_migrations(store, TEST_DIMS)
    assert second.applied == []
    assert (constraint_names(store), [(s.name, s.type) for s in index_states(store)]) == before


def test_schema_snapshot(store, snapshot):
    indexes = [(s.name, s.type, s.labels, s.properties, s.dimensions) for s in index_states(store)]
    assert {"constraints": constraint_names(store), "indexes": indexes} == snapshot


def test_all_indexes_online(store):
    assert all(s.state == "ONLINE" for s in index_states(store))
    assert vector_dimension_mismatches(store, TEST_DIMS) == []
    assert vector_dimension_mismatches(store, 1536)  # a config/index disagreement is detected


def test_uniqueness_constraints_enforced(store, project_id):
    store.write("CREATE (:File {uid: $u, project_id: $p})", u=f"{project_id}:file:a.ts", p=project_id)
    with pytest.raises(ConstraintError):
        store.write("CREATE (:File {uid: $u, project_id: $p})", u=f"{project_id}:file:a.ts", p=project_id)


def test_schema_markdown_lists_everything(store, project_id):
    md = schema_markdown(store, project_id)
    for needle in ("`Symbol`", "`CALLS`", "af_symbol_embedding", "af_symbol_uid", "`Service`"):
        assert needle in md
