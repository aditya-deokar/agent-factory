"""Evidence artifacts attached to features (spec §23)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...schema.model import EvidenceKind
from ..stores import Neo4jStore
from .knowledge_repo import now_iso


class EvidenceRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def upsert(self, rows: Sequence[Mapping[str, Any]], feature_uid: str | None = None) -> int:
        """rows: uid, kind, path|uri, content_hash, summary, ... (validated kind)."""
        clean = []
        for row in rows:
            props = {k: v for k, v in row.items() if k != "uid"}
            props["kind"] = EvidenceKind(props["kind"]).value
            props["project_id"] = self.project_id
            clean.append({"uid": row["uid"], "props": props})
        count = self.store.write_batches(
            """
            UNWIND $rows AS row
            MERGE (e:Evidence {uid: row.uid})
            ON CREATE SET e.created_at = $now
            SET e += row.props
            """,
            clean,
            now=now_iso(),
        )
        if feature_uid:
            self.store.write(
                "MATCH (f:Feature {uid: $f}) UNWIND $uids AS uid MATCH (e:Evidence {uid: uid}) "
                "MERGE (f)-[:HAS_EVIDENCE]->(e)",
                f=feature_uid,
                uids=[r["uid"] for r in clean],
            )
        return count

    def by_feature(self, feature_uid: str) -> list[dict[str, Any]]:
        rows = self.store.read(
            "MATCH (:Feature {uid: $f})-[:HAS_EVIDENCE]->(e:Evidence) RETURN e {.*} AS e ORDER BY e.created_at",
            f=feature_uid,
        )
        return [r["e"] for r in rows]

    def mark_stale(self, uids: list[str], reason: str) -> None:
        self.store.write(
            "UNWIND $uids AS uid MATCH (e:Evidence {uid: uid}) SET e.stale = true, e.stale_reason = $reason",
            uids=uids,
            reason=reason,
        )

    def stale_for_project(self) -> list[dict[str, Any]]:
        rows = self.store.read("MATCH (e:Evidence {project_id: $p, stale: true}) RETURN e {.*} AS e", p=self.project_id)
        return [r["e"] for r in rows]
