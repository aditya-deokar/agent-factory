"""Apply `schema/migrations/*.cypher` in order and record the schema version.

Statements use IF NOT EXISTS, so re-running is harmless; the SchemaVersion node
lets `doctor` report how far a database has been migrated.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib import resources
from typing import Any

from .stores import Neo4jStore

SCHEMA_ID = "domain"
_FILE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.cypher$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    statements: tuple[str, ...]


@dataclass
class MigrationReport:
    from_version: int
    to_version: int
    applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"from_version": self.from_version, "to_version": self.to_version, "applied": self.applied}


@dataclass(frozen=True)
class IndexState:
    name: str
    type: str
    state: str
    labels: tuple[str, ...]
    properties: tuple[str, ...]
    dimensions: int | None


def split_statements(text: str) -> list[str]:
    """Split a migration file into statements: `//` comment lines dropped, `;` at line end ends one."""
    lines = [line for line in text.splitlines() if not line.strip().startswith("//")]
    statements, current = [], []
    for line in lines:
        current.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(current).strip().rstrip(";").strip()
            if stmt:
                statements.append(stmt)
            current = []
    tail = "\n".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def load_migrations(dimensions: int) -> list[Migration]:
    if not 8 <= dimensions <= 4096:
        raise ValueError(f"embedding dimensions out of range: {dimensions}")
    package = resources.files("agent_factory.schema.migrations")
    found = []
    for entry in package.iterdir():
        m = _FILE.match(entry.name)
        if not m:
            continue
        text = entry.read_text(encoding="utf-8").replace("{{dims}}", str(int(dimensions)))
        found.append(Migration(int(m.group(1)), m.group(2), tuple(split_statements(text))))
    found.sort(key=lambda mig: mig.version)
    versions = [mig.version for mig in found]
    if versions != list(range(1, len(found) + 1)):
        raise RuntimeError(f"migration versions must be contiguous from 1, found {versions}")
    return found


def latest_version(dimensions: int = 1536) -> int:
    return len(load_migrations(dimensions))


def current_version(store: Neo4jStore) -> int:
    rows = store.read("MATCH (v:SchemaVersion {id: $id}) RETURN v.version AS version", id=SCHEMA_ID)
    return int(rows[0]["version"]) if rows and rows[0]["version"] is not None else 0


def apply_migrations(store: Neo4jStore, dimensions: int, wait_seconds: int = 300) -> MigrationReport:
    migrations = load_migrations(dimensions)
    start = current_version(store)
    report = MigrationReport(from_version=start, to_version=start)
    for mig in migrations:
        if mig.version <= start:
            continue
        for stmt in mig.statements:
            store.write(stmt)
        store.write(
            "MERGE (v:SchemaVersion {id: $id}) "
            "SET v.version = $version, v.applied_at = $at, v.dimensions = $dims, "
            "v.history = coalesce(v.history, []) + $name",
            id=SCHEMA_ID,
            version=mig.version,
            at=datetime.now(UTC).isoformat(),
            dims=dimensions,
            name=f"{mig.version:04d}_{mig.name}",
        )
        report.applied.append(f"{mig.version:04d}_{mig.name}")
        report.to_version = mig.version
    if report.applied:
        store.write(f"CALL db.awaitIndexes({int(wait_seconds)})")
    return report


def index_states(store: Neo4jStore, prefix: str = "af_") -> list[IndexState]:
    rows = store.read(
        "SHOW INDEXES YIELD name, type, state, labelsOrTypes, properties, options "
        "WHERE name STARTS WITH $prefix RETURN name, type, state, labelsOrTypes, properties, options",
        prefix=prefix,
    )
    states = []
    for r in rows:
        config = ((r.get("options") or {}).get("indexConfig")) or {}
        dims = config.get("vector.dimensions")
        states.append(
            IndexState(
                name=r["name"],
                type=r["type"],
                state=r["state"],
                labels=tuple(r["labelsOrTypes"] or ()),
                properties=tuple(r["properties"] or ()),
                dimensions=int(dims) if dims is not None else None,
            )
        )
    return sorted(states, key=lambda s: s.name)


def constraint_names(store: Neo4jStore, prefix: str = "af_") -> list[str]:
    rows = store.read("SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH $prefix RETURN name", prefix=prefix)
    return sorted(r["name"] for r in rows)


def vector_dimension_mismatches(store: Neo4jStore, dimensions: int) -> list[str]:
    return [
        f"{s.name} has {s.dimensions} dimensions, config says {dimensions}"
        for s in index_states(store)
        if s.type == "VECTOR" and s.dimensions is not None and s.dimensions != dimensions
    ]
