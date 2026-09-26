"""Neo4j access for the two graphs Agent Factory uses (spec §8.4).

- `domain`: the code knowledge graph (files, symbols, patterns, decisions,
  constraints, features, evidence). Configured by NEO4J_*.
- `memory`: the neo4j-agent-memory workspace (conversations, facts, traces).
  Configured by MVP_NEO4J_*. When unset or pointing at the same URI, both
  stores share one driver ("same-instance mode").

`read()` runs in a READ transaction, so a write smuggled into a read path is
rejected by the server. Text2Cypher and every MCP read tool rely on that.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

import neo4j
from neo4j import GraphDatabase, RoutingControl

from ..config import AgentFactoryConfig, ConnectionSettings, resolve_target

# Deprecation notices for vector-index procedures and retry chatter are noise for users.
logging.getLogger("neo4j.notifications").setLevel(logging.ERROR)
logging.getLogger("neo4j.session").setLevel(logging.ERROR)
logging.getLogger("neo4j.pool").setLevel(logging.ERROR)


class StoreUnavailable(RuntimeError):
    """The database cannot be reached (CLI exit code 3)."""


@dataclass(frozen=True)
class ServerInfo:
    agent: str
    version: str
    edition: str

    @property
    def version_tuple(self) -> tuple[int, ...]:
        parts = []
        for piece in self.version.split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            if not digits:
                break
            parts.append(int(digits))
        return tuple(parts)

    def supports_vector_index(self) -> bool:
        v = self.version_tuple
        return v >= (5, 11) or (len(v) > 0 and v[0] >= 2025)


class Neo4jStore:
    def __init__(self, driver: neo4j.Driver, database: str | None, name: str, settings: ConnectionSettings):
        self.driver = driver
        self.database = database
        self.name = name
        self.settings = settings

    def read(self, cypher: str, timeout: float | None = None, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = self.driver.execute_query(
            neo4j.Query(cypher, timeout=timeout),
            parameters_=params,
            database_=self.database,
            routing_=RoutingControl.READ,
        )
        return [r.data() for r in records]

    def write(self, cypher: str, timeout: float | None = None, **params: Any) -> list[dict[str, Any]]:
        records, _, _ = self.driver.execute_query(
            neo4j.Query(cypher, timeout=timeout),
            parameters_=params,
            database_=self.database,
            routing_=RoutingControl.WRITE,
        )
        return [r.data() for r in records]

    def write_batches(self, cypher: str, rows: Iterable[Any], size: int = 1000, **params: Any) -> int:
        """Run `cypher` (which must use `UNWIND $rows AS row`) over rows in batches."""
        batch: list[Any] = []
        total = 0
        for row in rows:
            batch.append(row)
            if len(batch) >= size:
                self.write(cypher, rows=batch, **params)
                total += len(batch)
                batch = []
        if batch:
            self.write(cypher, rows=batch, **params)
            total += len(batch)
        return total

    def explain_query_type(self, cypher: str, **params: Any) -> str:
        """`r`, `w`, `rw` or `s` from the server's EXPLAIN plan (nothing is executed)."""
        _, summary, _ = self.driver.execute_query(
            "EXPLAIN " + cypher, parameters_=params, database_=self.database, routing_=RoutingControl.READ
        )
        return str(summary.query_type)

    def verify(self) -> None:
        try:
            self.driver.verify_connectivity()
        except Exception as error:  # driver raises several unrelated types
            raise StoreUnavailable(
                f"{self.name} database unreachable at {self.settings.describe()}: {error}"
            ) from error

    def server_info(self) -> ServerInfo:
        rows = self.read("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition")
        agent = self.driver.get_server_info().agent
        row = next((r for r in rows if r["name"] == "Neo4j Kernel"), rows[0] if rows else None)
        if row is None:
            return ServerInfo(agent=agent, version="unknown", edition="unknown")
        return ServerInfo(agent=agent, version=row["versions"][0], edition=row["edition"])


class GraphStores:
    """The domain and memory stores. Use as a context manager or call close()."""

    def __init__(self, domain: Neo4jStore, memory: Neo4jStore | None, same_instance: bool):
        self.domain = domain
        self.memory = memory
        self.same_instance = same_instance

    @classmethod
    def from_settings(cls, domain: ConnectionSettings, memory: ConnectionSettings | None = None) -> GraphStores:
        domain_driver = _driver(domain)
        domain_store = Neo4jStore(domain_driver, domain.database, "domain", domain)
        if memory is None or memory.uri == domain.uri:
            settings = memory or domain
            return cls(domain_store, Neo4jStore(domain_driver, settings.database, "memory", settings), True)
        return cls(domain_store, Neo4jStore(_driver(memory), memory.database, "memory", memory), False)

    @classmethod
    def from_config(cls, config: AgentFactoryConfig, env: Mapping[str, str] | None = None) -> GraphStores:
        domain = resolve_target(config.neo4j.domain, env)
        if domain is None:
            raise StoreUnavailable(
                f"{config.neo4j.domain.uri_env} is not set. "
                "Add it to .env (see .env.example) and run agent-factory doctor"
            )
        return cls.from_settings(domain, resolve_target(config.neo4j.memory, env))

    def close(self) -> None:
        self.domain.driver.close()
        if self.memory is not None and not self.same_instance:
            self.memory.driver.close()

    def __enter__(self) -> GraphStores:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _driver(settings: ConnectionSettings) -> neo4j.Driver:
    # neo4j+s / neo4j+ssc / bolt+s carry their TLS policy in the scheme; nothing else to configure.
    return GraphDatabase.driver(
        settings.uri,
        auth=(settings.username, settings.password),
        connection_timeout=15,
        max_transaction_retry_time=15,
    )
