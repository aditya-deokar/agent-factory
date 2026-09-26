"""AuditRun nodes: when, at which commit, and what each audit produced."""

from __future__ import annotations

import json
from typing import Any

from ..stores import Neo4jStore


class AuditRunRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def start(self, uid: str, commit: str | None, mode: str, started_at: str) -> None:
        self.store.write(
            """
            MERGE (r:AuditRun {uid: $uid})
            SET r.project_id = $p, r.commit = $commit, r.mode = $mode, r.started_at = $started, r.status = 'running'
            WITH r MATCH (p:Project {id: $p}) MERGE (p)-[:CONTAINS]->(r)
            """,
            uid=uid,
            p=self.project_id,
            commit=commit,
            mode=mode,
            started=started_at,
        )

    def finish(self, uid: str, finished_at: str, stats: dict[str, Any], status: str = "done") -> None:
        self.store.write(
            "MATCH (r:AuditRun {uid: $uid}) SET r.finished_at = $at, r.stats = $stats, r.status = $status",
            uid=uid,
            at=finished_at,
            stats=json.dumps(stats, sort_keys=True, default=str),
            status=status,
        )

    def last(self) -> dict[str, Any] | None:
        rows = self.store.read(
            """
            MATCH (r:AuditRun {project_id: $p, status: 'done'})
            RETURN r {.*} AS r ORDER BY r.finished_at DESC LIMIT 1
            """,
            p=self.project_id,
        )
        if not rows:
            return None
        run = dict(rows[0]["r"])
        run["stats"] = json.loads(run.get("stats") or "{}")
        return run
