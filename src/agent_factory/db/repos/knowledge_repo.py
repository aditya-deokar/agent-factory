"""Knowledge (Pattern / Decision / Constraint), its evidence, and its audit events."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from ...schema.model import KnowledgeKind, KnowledgeStatus, Label, Rel, rel
from ...schema.uids import doc_uid, evidence_uid, file_uid
from ..stores import Neo4jStore
from .code_repo import endpoint_label

_KNOWLEDGE_LINKS = frozenset(
    {Rel.SUPERSEDES, Rel.ESTABLISHES, Rel.CONSTRAINED_BY, Rel.FOLLOWS, Rel.FOLLOWED, Rel.CREATED}
)
_EVIDENCE_RELS = frozenset({Rel.SUPPORTED_BY, Rel.CONTRADICTED_BY})
_KIND_LABELS = ":".join(k.label.value for k in KnowledgeKind)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


class KnowledgeRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    def upsert(self, item: Mapping[str, Any]) -> None:
        """item: uid, kind, and any Knowledge properties. created_at is kept on update."""
        kind = KnowledgeKind(item["kind"])
        props = {k: v for k, v in item.items() if k not in ("uid", "embedding")}
        props["project_id"] = self.project_id
        props["updated_at"] = now_iso()
        self.store.write(
            f"""
            MERGE (k:Knowledge {{uid: $uid}})
            ON CREATE SET k.created_at = $now
            REMOVE k:{_KIND_LABELS}
            SET k += $props, k:{kind.label.value}
            """,
            uid=item["uid"],
            props=props,
            now=props["updated_at"],
        )

    def get(self, uid: str) -> dict[str, Any] | None:
        rows = self.store.read("MATCH (k:Knowledge {uid: $uid}) RETURN k {.*, embedding: null} AS k", uid=uid)
        return _clean(rows[0]["k"]) if rows else None

    def list_knowledge(
        self,
        kind: KnowledgeKind | str | None = None,
        status: KnowledgeStatus | str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        rows = self.store.read(
            """
            MATCH (k:Knowledge {project_id: $p})
            WHERE ($kind IS NULL OR k.kind = $kind) AND ($status IS NULL OR k.status = $status)
            OPTIONAL MATCH (k)-[:SUPPORTED_BY]->(e:Evidence)
            WITH k, count(e) AS evidence_count
            RETURN k {.*, embedding: null} AS k, evidence_count
            ORDER BY k.kind, k.status, k.confidence DESC, k.title
            LIMIT $limit
            """,
            p=self.project_id,
            kind=KnowledgeKind(kind).value if kind else None,
            status=KnowledgeStatus(status).value if status else None,
            limit=limit,
        )
        return [{**_clean(r["k"]), "evidence_count": r["evidence_count"]} for r in rows]

    def set_status(self, uid: str, status: KnowledgeStatus | str, **props: Any) -> str | None:
        """Set status (+ extra props). Returns the previous status. Guards live in memory/lifecycle.py."""
        rows = self.store.write(
            """
            MATCH (k:Knowledge {uid: $uid})
            WITH k, k.status AS previous
            SET k.status = $status, k.updated_at = $now, k += $props
            RETURN previous
            """,
            uid=uid,
            status=KnowledgeStatus(status).value,
            now=now_iso(),
            props=props,
        )
        if not rows:
            raise KeyError(f"knowledge {uid} not found")
        return rows[0]["previous"]

    def update(self, uid: str, **props: Any) -> None:
        self.store.write(
            "MATCH (k:Knowledge {uid: $uid}) SET k += $props, k.updated_at = $now",
            uid=uid,
            props=props,
            now=now_iso(),
        )

    # -- evidence --------------------------------------------------------------

    def attach_evidence(
        self, knowledge_uid: str, evidence: Sequence[Mapping[str, Any]], rel_type: Rel = Rel.SUPPORTED_BY
    ) -> int:
        """evidence rows: kind, path, line_start, line_end, symbol_uid, content_hash, commit, note, summary."""
        rt = rel(rel_type)
        if rt not in _EVIDENCE_RELS:
            raise ValueError(f"{rt} is not an evidence relationship")
        rows = []
        for ev in evidence:
            path = ev.get("path")
            uid = ev.get("uid") or evidence_uid(
                self.project_id,
                knowledge_uid,
                rt.value,
                str(path),
                str(ev.get("line_start")),
                str(ev.get("line_end")),
                str(ev.get("symbol_uid")),
            )
            rows.append(
                {
                    "uid": uid,
                    "props": {
                        "kind": ev.get("kind", "code_ref"),
                        "path": path,
                        "line_start": ev.get("line_start"),
                        "line_end": ev.get("line_end"),
                        "symbol_uid": ev.get("symbol_uid"),
                        "content_hash": ev.get("content_hash"),
                        "commit": ev.get("commit"),
                        "note": ev.get("note"),
                        "summary": ev.get("summary"),
                        "stale": bool(ev.get("stale", False)),
                        "project_id": self.project_id,
                    },
                    "file_uid": file_uid(self.project_id, path) if path else None,
                    "doc_uid": doc_uid(self.project_id, path) if path else None,
                    "symbol_uid": ev.get("symbol_uid"),
                }
            )
        return self.store.write_batches(
            f"""
            UNWIND $rows AS row
            MATCH (k:Knowledge {{uid: $k}})
            MERGE (e:Evidence {{uid: row.uid}})
            ON CREATE SET e.created_at = $now
            SET e += row.props
            MERGE (k)-[:{rt.value}]->(e)
            WITH e, row
            OPTIONAL MATCH (f:File {{uid: row.file_uid}})
            OPTIONAL MATCH (s:Symbol {{uid: row.symbol_uid}})
            OPTIONAL MATCH (d:Doc {{uid: row.doc_uid}})
            FOREACH (x IN CASE WHEN f IS NULL THEN [] ELSE [f] END | MERGE (e)-[:REFERENCES]->(x))
            FOREACH (x IN CASE WHEN s IS NULL THEN [] ELSE [s] END | MERGE (e)-[:REFERENCES]->(x))
            FOREACH (x IN CASE WHEN d IS NULL THEN [] ELSE [d] END | MERGE (e)-[:REFERENCES]->(x))
            """,
            rows,
            k=knowledge_uid,
            now=now_iso(),
        )

    def evidence_of(self, uid: str) -> list[dict[str, Any]]:
        rows = self.store.read(
            """
            MATCH (k:Knowledge {uid: $uid})-[r:SUPPORTED_BY|CONTRADICTED_BY]->(e:Evidence)
            RETURN type(r) AS rel, e {.*} AS e
            ORDER BY rel DESC, e.path, e.line_start
            """,
            uid=uid,
        )
        return [{**_clean(r["e"]), "rel": r["rel"]} for r in rows]

    def clear_evidence(self, uid: str, rel_type: Rel) -> int:
        """Remove one kind of evidence (e.g. CONTRADICTED_BY before recomputing violations)."""
        rt = rel(rel_type)
        if rt not in _EVIDENCE_RELS:
            raise ValueError(f"{rt} is not an evidence relationship")
        rows = self.store.write(
            f"""
            MATCH (k:Knowledge {{uid: $uid}})-[r:{rt.value}]->(e:Evidence)
            DELETE r
            WITH e WHERE NOT (e)<-[:SUPPORTED_BY|CONTRADICTED_BY|HAS_EVIDENCE]-()
            DETACH DELETE e
            RETURN count(*) AS n
            """,
            uid=uid,
        )
        return sum(r["n"] for r in rows)

    def mark_evidence(self, evidence_uid_: str, **props: Any) -> None:
        self.store.write("MATCH (e:Evidence {uid: $uid}) SET e += $props", uid=evidence_uid_, props=props)

    # -- links -----------------------------------------------------------------

    def link(
        self,
        src_uid: str,
        rel_type: Rel | str,
        dst_uid: str,
        src_label: Label | str = Label.KNOWLEDGE,
        dst_label: Label | str = Label.KNOWLEDGE,
        **props: Any,
    ) -> None:
        rt = rel(rel_type)
        if rt not in _KNOWLEDGE_LINKS:
            raise ValueError(f"{rt} is not a knowledge relationship")
        self.store.write(
            f"""
            MATCH (a:{endpoint_label(src_label)} {{uid: $src}}), (b:{endpoint_label(dst_label)} {{uid: $dst}})
            MERGE (a)-[r:{rt.value}]->(b) SET r += $props
            """,
            src=src_uid,
            dst=dst_uid,
            props=props,
        )

    def link_many(
        self, rel_type: Rel | str, rows: Sequence[Mapping[str, Any]], src_label: Label | str, dst_label: Label | str
    ) -> int:
        """rows: {src, dst, props?} for FOLLOWS / CONSTRAINED_BY / ESTABLISHES fan-out."""
        rt = rel(rel_type)
        if rt not in _KNOWLEDGE_LINKS:
            raise ValueError(f"{rt} is not a knowledge relationship")
        return self.store.write_batches(
            f"""
            UNWIND $rows AS row
            MATCH (a:{endpoint_label(src_label)} {{uid: row.src}}), (b:{endpoint_label(dst_label)} {{uid: row.dst}})
            MERGE (a)-[r:{rt.value}]->(b) SET r += coalesce(row.props, {{}})
            """,
            rows,
        )

    def unlink_all(self, uid: str, rel_type: Rel | str, incoming: bool = True) -> None:
        rt = rel(rel_type)
        if rt not in _KNOWLEDGE_LINKS:
            raise ValueError(f"{rt} is not a knowledge relationship")
        pattern = f"()-[r:{rt.value}]->(k)" if incoming else f"(k)-[r:{rt.value}]->()"
        self.store.write(f"MATCH (k:Knowledge {{uid: $uid}}) MATCH {pattern} DELETE r", uid=uid)

    # -- events (audit log) ----------------------------------------------------

    def record_event(self, event: Mapping[str, Any]) -> None:
        self.store.write(
            """
            CREATE (ev:MemoryEvent)
            SET ev = $event, ev.project_id = $p
            WITH ev
            OPTIONAL MATCH (k:Knowledge {uid: $event.target_uid})
            FOREACH (x IN CASE WHEN k IS NULL THEN [] ELSE [k] END | MERGE (ev)-[:REFERENCES]->(x))
            """,
            event=dict(event),
            p=self.project_id,
        )

    def events(self, uid: str | None = None, since: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.store.read(
            """
            MATCH (ev:MemoryEvent {project_id: $p})
            WHERE ($uid IS NULL OR ev.target_uid = $uid) AND ($since IS NULL OR ev.at >= $since)
            RETURN ev {.*} AS ev ORDER BY ev.at DESC LIMIT $limit
            """,
            p=self.project_id,
            uid=uid,
            since=since,
            limit=limit,
        )
        return [r["ev"] for r in rows]

    # -- deletion & stats ------------------------------------------------------

    def delete(self, uid: str) -> bool:
        rows = self.store.write(
            """
            MATCH (k:Knowledge {uid: $uid})
            OPTIONAL MATCH (k)-[:SUPPORTED_BY|CONTRADICTED_BY]->(e:Evidence)
            WITH k, collect(e) AS evidence
            DETACH DELETE k
            WITH evidence
            UNWIND evidence AS e
            WITH e WHERE NOT (e)<-[:SUPPORTED_BY|CONTRADICTED_BY|HAS_EVIDENCE]-()
            DETACH DELETE e
            RETURN count(*) AS n
            """,
            uid=uid,
        )
        return bool(rows) or self.get(uid) is None

    def counts(self) -> dict[str, dict[str, int]]:
        rows = self.store.read(
            "MATCH (k:Knowledge {project_id: $p}) RETURN k.kind AS kind, k.status AS status, count(*) AS n",
            p=self.project_id,
        )
        out: dict[str, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["kind"], {})[r["status"]] = r["n"]
        return out


def _clean(props: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in props.items() if k != "embedding"}
