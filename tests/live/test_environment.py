"""Live environment checks, ported from workshop-agent-memory/test_environment.py.

Opt-in: `uv run pytest -m live`. Reads AF_LIVE_ENV_FILE (default `.env.live`, else `.env`).
Every check here is READ-ONLY: the memory workspace may be a shared instance.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import dotenv_values

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def env() -> dict[str, str]:
    path = ROOT / os.environ.get("AF_LIVE_ENV_FILE", ".env.live")
    if not path.exists():
        path = ROOT / ".env"
    if not path.exists():
        pytest.skip("no .env.live or .env file")
    return {k: v for k, v in dotenv_values(path).items() if v}


def _require(env: dict[str, str], *names: str) -> None:
    missing = [n for n in names if n not in env]
    if missing:
        pytest.skip(f"not configured: {', '.join(missing)}")


def test_openai_variables(env):
    _require(env, "OPENAI_API_KEY")
    assert env.get("OPENAI_BASE_URL"), "OPENAI_BASE_URL missing: a workshop key sent to OpenAI is rejected"


def test_domain_neo4j_connection(env):
    _require(env, "NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD")
    from neo4j import GraphDatabase, RoutingControl

    with GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"])) as d:
        d.verify_connectivity()
        recs, _, _ = d.execute_query(
            "RETURN 1 AS ok", database_=env.get("NEO4J_DATABASE") or None, routing_=RoutingControl.READ
        )
        assert recs[0]["ok"] == 1


def test_memory_workspace_connection(env):
    _require(env, "MVP_NEO4J_URI", "MVP_NEO4J_USERNAME", "MVP_NEO4J_PASSWORD")
    from neo4j import GraphDatabase

    with GraphDatabase.driver(env["MVP_NEO4J_URI"], auth=(env["MVP_NEO4J_USERNAME"], env["MVP_NEO4J_PASSWORD"])) as d:
        d.verify_connectivity()


def test_agent_memory_installed():
    import neo4j_agent_memory  # noqa: F401
    from neo4j_agent_memory.nams import _unsupported  # noqa: F401  (needs the [nams] extra, see spike S2)


def test_embeddings_endpoint(env):
    _require(env, "OPENAI_API_KEY", "OPENAI_BASE_URL")
    from openai import OpenAI

    client = OpenAI(api_key=env["OPENAI_API_KEY"], base_url=env["OPENAI_BASE_URL"])
    try:
        out = client.embeddings.create(model="text-embedding-3-small", input=["ping"])
    except Exception as error:  # the proxy explains itself (e.g. idle session): show its message
        detail = getattr(error, "body", None)
        pytest.fail(f"embeddings call failed: {detail or error}")
    assert len(out.data[0].embedding) == 1536
