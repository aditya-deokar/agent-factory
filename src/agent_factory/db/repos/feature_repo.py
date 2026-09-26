"""Feature memory (spec §9.6): what each feature changed, reused, introduced and proved."""

from __future__ import annotations

from typing import Any

from ...schema.model import FEATURE_RELS, FeatureStatus, Rel, rel
from ..stores import Neo4jStore
from .knowledge_repo import now_iso


class FeatureRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def create(self, uid: str, name: str, request: str, **props: Any) -> None:
        self.store.write(
            """
            MERGE (f:Feature {uid: $uid})
            ON CREATE SET f.created_at = $now, f.status = $status
            SET f.name = $name, f.request = $request, f.project_id = $p, f += $props
            WITH f
            MATCH (p:Project {id: $p})
            MERGE (p)-[:CONTAINS]->(f)
            """,
            uid=uid,
            name=name,
            request=request,
            p=self.project_id,
            props=props,
            now=now_iso(),
            status=FeatureStatus.PLANNING.value,
        )

    def update(self, uid: str, **props: Any) -> None:
        if "status" in props:
            props["status"] = FeatureStatus(props["status"]).value
        self.store.write(
            "MATCH (f:Feature {uid: $uid}) SET f += $props, f.updated_at = $now", uid=uid, props=props, now=now_iso()
        )

    def get(self, uid: str) -> dict[str, Any] | None:
        rows = self.store.read("MATCH (f:Feature {uid: $uid}) RETURN f {.*} AS f", uid=uid)
        return rows[0]["f"] if rows else None

    def list_features(self, status: FeatureStatus | str | None = None) -> list[dict[str, Any]]:
        rows = self.store.read(
            "MATCH (f:Feature {project_id: $p}) WHERE $status IS NULL OR f.status = $status "
            "RETURN f {.*} AS f ORDER BY f.created_at DESC",
            p=self.project_id,
            status=FeatureStatus(status).value if status else None,
        )
        return [r["f"] for r in rows]

    def link(self, feature_uid: str, rel_type: Rel | str, target_uids: list[str], **props: Any) -> int:
        rt = rel(rel_type)
        if rt not in FEATURE_RELS:
            raise ValueError(f"{rt} is not a feature relationship")
        rows = self.store.write(
            f"""
            MATCH (f:Feature {{uid: $uid}})
            UNWIND $targets AS t
            MATCH (x {{uid: t}})
            MERGE (f)-[r:{rt.value}]->(x) SET r += $props
            RETURN count(r) AS n
            """,
            uid=feature_uid,
            targets=target_uids,
            props=props,
        )
        return sum(r["n"] for r in rows)

    def links(self, feature_uid: str) -> dict[str, list[str]]:
        rows = self.store.read(
            "MATCH (f:Feature {uid: $uid})-[r]->(x) WHERE x.uid IS NOT NULL "
            "RETURN type(r) AS rel, collect(x.uid) AS targets",
            uid=feature_uid,
        )
        return {r["rel"]: sorted(r["targets"]) for r in rows}
