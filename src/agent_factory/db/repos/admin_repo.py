"""Whole-project operations: stats, purge, export/import (spec §30: inspect and delete memory)."""

from __future__ import annotations

from typing import Any

from ...schema.model import Label, Rel, Role
from ..stores import Neo4jStore

_ALLOWED_LABELS = {lbl.value for lbl in Label} | {r.value for r in Role}
_ALLOWED_RELS = {r.value for r in Rel}


class AdminRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def stats(self) -> dict[str, Any]:
        labels = self.store.read(
            """
            MATCH (n {project_id: $p})
            UNWIND labels(n) AS label
            RETURN label, count(*) AS n ORDER BY label
            """,
            p=self.project_id,
        )
        rels = self.store.read(
            """
            MATCH (a {project_id: $p})-[r]->()
            RETURN type(r) AS rel, count(*) AS n ORDER BY rel
            """,
            p=self.project_id,
        )
        return {
            "project_id": self.project_id,
            "labels": {r["label"]: r["n"] for r in labels},
            "relationships": {r["rel"]: r["n"] for r in rels},
        }

    def purge_project(self, batch: int = 5000) -> int:
        """Delete every node of the project (and the Project node). Returns nodes deleted."""
        total = 0
        while True:
            rows = self.store.write(
                "MATCH (n {project_id: $p}) WITH n LIMIT $batch DETACH DELETE n RETURN count(*) AS n",
                p=self.project_id,
                batch=batch,
            )
            deleted = sum(r["n"] for r in rows)
            total += deleted
            if deleted < batch:
                break
        rows = self.store.write("MATCH (p:Project {id: $p}) DETACH DELETE p RETURN count(*) AS n", p=self.project_id)
        return total + sum(r["n"] for r in rows)

    def export(self, include_embeddings: bool = False, labels: list[str] | None = None) -> dict[str, Any]:
        """Nodes and relationships of the project as plain JSON-able data."""
        wanted = [lbl for lbl in (labels or []) if lbl in _ALLOWED_LABELS]
        nodes = self.store.read(
            """
            MATCH (n {project_id: $p})
            WHERE size($labels) = 0 OR any(l IN labels(n) WHERE l IN $labels)
            RETURN labels(n) AS labels, properties(n) AS props
            ORDER BY n.uid
            """,
            p=self.project_id,
            labels=wanted,
        )
        uids = [n["props"].get("uid") for n in nodes if n["props"].get("uid")]
        rels = self.store.read(
            """
            UNWIND $uids AS uid
            MATCH (a {uid: uid})-[r]->(b)
            WHERE b.uid IN $uids
            RETURN a.uid AS src, type(r) AS type, b.uid AS dst, properties(r) AS props
            ORDER BY src, type, dst
            """,
            uids=uids,
        )
        out_nodes = []
        for n in nodes:
            props = dict(n["props"])
            if not include_embeddings:
                props.pop("embedding", None)
            out_nodes.append({"labels": sorted(n["labels"]), "props": props})
        return {"project_id": self.project_id, "nodes": out_nodes, "relationships": [dict(r) for r in rels]}

    def import_(self, data: dict[str, Any]) -> dict[str, int]:
        """Recreate an export. Labels and relationship types are checked against the schema."""
        if data.get("project_id") != self.project_id:
            raise ValueError(f"export is for project {data.get('project_id')!r}, not {self.project_id!r}")
        created = 0
        for node in data.get("nodes", []):
            labels = [lbl for lbl in node["labels"] if lbl in _ALLOWED_LABELS]
            if not labels:
                continue
            props = node["props"]
            if "Project" in labels and "id" in props:
                self.store.write("MERGE (n:Project {id: $id}) SET n = $props", id=props["id"], props=props)
                created += 1
                continue
            if "uid" not in props:
                continue
            self.store.write(
                f"MERGE (n:{labels[0]} {{uid: $uid}}) SET n = $props" + "".join(f" SET n:{lbl}" for lbl in labels[1:]),
                uid=props["uid"],
                props=props,
            )
            created += 1
        linked = 0
        by_type: dict[str, list[dict[str, Any]]] = {}
        for rel in data.get("relationships", []):
            if rel["type"] in _ALLOWED_RELS:
                by_type.setdefault(rel["type"], []).append(rel)
        for rel_type, rows in by_type.items():
            linked += self.store.write_batches(
                f"UNWIND $rows AS row MATCH (a {{uid: row.src}}), (b {{uid: row.dst}}) "
                f"MERGE (a)-[r:{rel_type}]->(b) SET r = coalesce(row.props, {{}})",
                rows,
            )
        return {"nodes": created, "relationships": linked}
