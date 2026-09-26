"""Neo4j for integration tests.

AF_TEST_NEO4J_URI (+ AF_TEST_NEO4J_USER / AF_TEST_NEO4J_PASSWORD) points at an existing
database: the CI service container, or `docker compose up` locally. Otherwise a
session-scoped testcontainers Neo4j 5.26 is started.

Tests isolate themselves with a unique project_id rather than wiping the database,
so they can share one instance (and run in parallel).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest

from agent_factory.config import ConnectionSettings
from agent_factory.db import GraphStores, Neo4jStore
from agent_factory.db.migrations import apply_migrations
from agent_factory.db.repos import AdminRepo

TEST_DIMS = 64  # small vectors keep the fixtures fast; production uses 1536


@pytest.fixture(scope="session")
def neo4j_settings() -> Iterator[ConnectionSettings]:
    uri = os.environ.get("AF_TEST_NEO4J_URI")
    if uri:
        yield ConnectionSettings(
            uri,
            os.environ.get("AF_TEST_NEO4J_USER", "neo4j"),
            os.environ.get("AF_TEST_NEO4J_PASSWORD", "agentfactory"),
            os.environ.get("AF_TEST_NEO4J_DATABASE") or None,
        )
        return
    try:
        from testcontainers.community.neo4j import Neo4jContainer
    except ImportError:  # pragma: no cover
        pytest.skip("testcontainers not installed and AF_TEST_NEO4J_URI not set")
    try:
        container = Neo4jContainer("neo4j:5.26-community", password="agentfactory-test")
        container.start()
    except Exception as error:  # pragma: no cover - no Docker available
        pytest.skip(f"cannot start Neo4j container ({error}); set AF_TEST_NEO4J_URI")
    try:
        yield ConnectionSettings(container.get_connection_url(), "neo4j", "agentfactory-test", None)
    finally:
        container.stop()


@pytest.fixture(scope="session")
def stores(neo4j_settings: ConnectionSettings) -> Iterator[GraphStores]:
    s = GraphStores.from_settings(neo4j_settings)
    s.domain.verify()
    _ensure_test_dims(s.domain)
    apply_migrations(s.domain, TEST_DIMS)
    yield s
    s.close()


@pytest.fixture
def store(stores: GraphStores) -> Neo4jStore:
    return stores.domain


@pytest.fixture
def project_id(store: Neo4jStore) -> Iterator[str]:
    pid = f"t{uuid.uuid4().hex[:10]}"
    store.write("MERGE (p:Project {id: $id}) SET p.name = $id", id=pid)
    yield pid
    AdminRepo(store, pid).purge_project()


def _ensure_test_dims(store: Neo4jStore) -> None:
    """A dev database may carry 1536-d indexes; tests need TEST_DIMS. Rebuild vector indexes if needed."""
    from agent_factory.db.migrations import index_states

    wrong = [s.name for s in index_states(store) if s.type == "VECTOR" and s.dimensions not in (None, TEST_DIMS)]
    if wrong:
        drop_schema(store)


def drop_schema(store: Neo4jStore) -> None:
    from agent_factory.db.migrations import constraint_names, index_states

    for name in constraint_names(store):
        store.write(f"DROP CONSTRAINT {name} IF EXISTS")
    for s in index_states(store):
        store.write(f"DROP INDEX {s.name} IF EXISTS")
    store.write("MATCH (v:SchemaVersion) DETACH DELETE v")
