"""Historical memory: commits, the files they touched, and co-change coupling."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..stores import Neo4jStore


class HistoryRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def upsert_commits(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """rows: uid, sha, message, type, author_hash, date, file_count"""
        return self.store.write_batches(
            """
            UNWIND $rows AS row
            MERGE (c:Commit {uid: row.uid})
            SET c += row, c.project_id = $p
            """,
            rows,
            p=self.project_id,
        )

    def upsert_touches(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """rows: commit_uid, file_uid, added, deleted. Files that no longer exist are skipped."""
        return self.store.write_batches(
            """
            UNWIND $rows AS row
            MATCH (c:Commit {uid: row.commit_uid}), (f:File {uid: row.file_uid})
            MERGE (c)-[r:TOUCHES]->(f) SET r.added = row.added, r.deleted = row.deleted
            """,
            rows,
        )

    def known_commits(self) -> set[str]:
        rows = self.store.read("MATCH (c:Commit {project_id: $p}) RETURN c.sha AS sha", p=self.project_id)
        return {r["sha"] for r in rows}

    def recompute_co_changes(self, min_count: int = 3, max_files: int = 50) -> int:
        """File pairs changed together in >= min_count commits (big sweeping commits ignored)."""
        self.store.write("MATCH (a:File {project_id: $p})-[r:CO_CHANGES]->() DELETE r", p=self.project_id)
        rows = self.store.write(
            """
            MATCH (c:Commit {project_id: $p}) WHERE coalesce(c.file_count, 0) <= $max
            MATCH (c)-[:TOUCHES]->(a:File), (c)-[:TOUCHES]->(b:File)
            WHERE a.uid < b.uid
            WITH a, b, count(c) AS n WHERE n >= $min
            MERGE (a)-[r:CO_CHANGES]->(b) SET r.count = n
            RETURN count(r) AS pairs
            """,
            p=self.project_id,
            min=min_count,
            max=max_files,
        )
        return sum(r["pairs"] for r in rows)
