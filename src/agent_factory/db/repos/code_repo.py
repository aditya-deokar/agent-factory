"""Code structure: projects, modules, files, symbols and their edges."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ...schema.model import Label, Rel, Role, rel, role
from ..stores import Neo4jStore

# Edges owned by the auditor: refreshed on every audit of their source node.
CODE_RELS: tuple[Rel, ...] = (
    Rel.CONTAINS,
    Rel.DEFINES,
    Rel.HAS_MEMBER,
    Rel.IMPORTS,
    Rel.DEPENDS_ON,
    Rel.USES,
    Rel.CALLS,
    Rel.EXTENDS,
    Rel.IMPLEMENTS,
    Rel.ACCESSES,
    Rel.HANDLES,
    Rel.COVERS,
    Rel.FOLLOWS,
    Rel.DESCRIBES,
    Rel.FROM_DOC,
    Rel.NEXT_CHUNK,
)
_ENDPOINT_LABELS = {
    Label.PROJECT,
    Label.MODULE,
    Label.FILE,
    Label.SYMBOL,
    Label.DOC,
    Label.DOC_CHUNK,
    Label.KNOWLEDGE,
    Label.COMMIT,
    Label.FEATURE,
    Label.EVIDENCE,
}
_ALL_ROLE_LABELS = ":".join(r.value for r in Role)


def endpoint_label(value: Label | str) -> str:
    lbl = Label(value)
    if lbl not in _ENDPOINT_LABELS:
        raise ValueError(f"label {lbl} is not an edge endpoint")
    return lbl.value


class CodeRepo:
    def __init__(self, store: Neo4jStore, project_id: str):
        self.store = store
        self.project_id = project_id

    # -- nodes -----------------------------------------------------------------

    def upsert_project(self, name: str, languages: list[str], **props: Any) -> None:
        self.store.write(
            "MERGE (p:Project {id: $id}) SET p.project_id = $id, p.name = $name, p.languages = $languages, p += $props",
            id=self.project_id,
            name=name,
            languages=languages,
            props=props,
        )

    def upsert_modules(self, rows: Sequence[Mapping[str, Any]], run_uid: str) -> int:
        """rows: {uid, path, kind, parent_uid (None: attached to the Project)}"""
        count = self.store.write_batches(
            """
            UNWIND $rows AS row
            MERGE (m:Module {uid: row.uid})
            SET m.project_id = $project, m.path = row.path, m.kind = row.kind, m.last_audit_run = $run
            """,
            rows,
            project=self.project_id,
            run=run_uid,
        )
        self.store.write_batches(
            """
            UNWIND $rows AS row
            MATCH (p:Project {id: $project}), (m:Module {uid: row.uid})
            MERGE (p)-[r:CONTAINS]->(m) SET r.last_audit_run = $run
            """,
            [r for r in rows if r.get("parent_uid") is None],
            project=self.project_id,
            run=run_uid,
        )
        self.store.write_batches(
            """
            UNWIND $rows AS row
            MATCH (parent:Module {uid: row.parent_uid}), (m:Module {uid: row.uid})
            MERGE (parent)-[r:CONTAINS]->(m) SET r.last_audit_run = $run
            """,
            [r for r in rows if r.get("parent_uid") is not None],
            run=run_uid,
        )
        return count

    def upsert_files(self, rows: Iterable[Mapping[str, Any]], run_uid: str) -> int:
        """rows: {uid, path, lang, sha256, loc, module_uid, is_test}"""
        return self.store.write_batches(
            """
            UNWIND $rows AS row
            MERGE (f:File {uid: row.uid})
            SET f.project_id = $project, f.path = row.path, f.lang = row.lang, f.sha256 = row.sha256,
                f.loc = row.loc, f.last_audit_run = $run
            FOREACH (_ IN CASE WHEN row.is_test THEN [1] ELSE [] END | SET f:TestFile)
            FOREACH (_ IN CASE WHEN row.is_test THEN [] ELSE [1] END | REMOVE f:TestFile)
            WITH f, row WHERE row.module_uid IS NOT NULL
            MATCH (m:Module {uid: row.module_uid})
            MERGE (m)-[r:CONTAINS]->(f) SET r.last_audit_run = $run
            """,
            rows,
            project=self.project_id,
            run=run_uid,
        )

    def upsert_symbols(self, rows: Sequence[Mapping[str, Any]], run_uid: str) -> int:
        """rows: {uid, file_uid, props: {...}, roles: [Role...], role_confidence}"""
        count = self.store.write_batches(
            f"""
            UNWIND $rows AS row
            MERGE (s:Symbol {{uid: row.uid}})
            REMOVE s:{_ALL_ROLE_LABELS}
            SET s += row.props, s.project_id = $project, s.roles = row.roles,
                s.role_confidence = row.role_confidence, s.last_audit_run = $run
            WITH s, row
            MATCH (f:File {{uid: row.file_uid}})
            MERGE (f)-[r:DEFINES]->(s) SET r.last_audit_run = $run
            """,
            rows,
            project=self.project_id,
            run=run_uid,
        )
        by_role: dict[Role, list[str]] = {}
        for row in rows:
            for r in row.get("roles") or []:
                by_role.setdefault(role(r), []).append(row["uid"])
        for r, uids in by_role.items():
            self.store.write_batches(f"UNWIND $rows AS uid MATCH (s:Symbol {{uid: uid}}) SET s:{r.value}", uids)
        return count

    def set_embeddings(self, label: Label, rows: Iterable[Mapping[str, Any]]) -> int:
        """rows: {uid, embedding, model?}. Only Symbol, DocChunk and Knowledge carry embeddings."""
        if label not in (Label.SYMBOL, Label.DOC_CHUNK, Label.KNOWLEDGE):
            raise ValueError(f"{label} has no embedding")
        return self.store.write_batches(
            f"UNWIND $rows AS row MATCH (n:{label.value} {{uid: row.uid}}) "
            "SET n.embedding = row.embedding, n.embedding_model = row.model",
            rows,
            size=200,
        )

    def upsert_docs(self, rows: Sequence[Mapping[str, Any]], run_uid: str) -> int:
        """rows: {uid, path, kind, title}"""
        return self.store.write_batches(
            """
            UNWIND $rows AS row
            MERGE (d:Doc {uid: row.uid})
            SET d.project_id = $project, d.path = row.path, d.kind = row.kind, d.title = row.title,
                d.last_audit_run = $run
            WITH d
            MATCH (p:Project {id: $project})
            MERGE (p)-[:CONTAINS]->(d)
            """,
            rows,
            project=self.project_id,
            run=run_uid,
        )

    def upsert_chunks(self, rows: Sequence[Mapping[str, Any]], run_uid: str) -> int:
        """rows: {uid, doc_uid, index, text, heading, line_start, prev_uid}"""
        count = self.store.write_batches(
            """
            UNWIND $rows AS row
            MERGE (c:DocChunk {uid: row.uid})
            SET c.project_id = $project, c.index = row.index, c.text = row.text, c.heading = row.heading,
                c.line_start = row.line_start, c.last_audit_run = $run
            WITH c, row
            MATCH (d:Doc {uid: row.doc_uid})
            MERGE (c)-[r:FROM_DOC]->(d) SET r.last_audit_run = $run
            """,
            rows,
            project=self.project_id,
            run=run_uid,
        )
        self.store.write_batches(
            """
            UNWIND $rows AS row
            MATCH (a:DocChunk {uid: row.prev_uid}), (b:DocChunk {uid: row.uid})
            MERGE (a)-[r:NEXT_CHUNK]->(b) SET r.last_audit_run = $run
            """,
            [r for r in rows if r.get("prev_uid")],
            run=run_uid,
        )
        return count

    def delete_stale_docs(self, run_uid: str) -> int:
        rows = self.store.write(
            """
            MATCH (n {project_id: $p}) WHERE (n:Doc OR n:DocChunk) AND coalesce(n.last_audit_run, '') <> $run
            DETACH DELETE n RETURN count(*) AS n
            """,
            p=self.project_id,
            run=run_uid,
        )
        return sum(r["n"] for r in rows)

    def delete_stale_symbols(self, file_uids: list[str], run_uid: str) -> int:
        """Symbols that disappeared from re-audited files."""
        if not file_uids:
            return 0
        self.store.write(
            """
            UNWIND $uids AS uid
            MATCH (:File {uid: uid})-[:DEFINES]->(s:Symbol) WHERE coalesce(s.last_audit_run, '') <> $run
            MATCH (e:Evidence)-[:REFERENCES]->(s)
            SET e.stale = true, e.stale_reason = 'target_deleted'
            """,
            uids=file_uids,
            run=run_uid,
        )
        rows = self.store.write(
            """
            UNWIND $uids AS uid
            MATCH (:File {uid: uid})-[:DEFINES]->(s:Symbol) WHERE coalesce(s.last_audit_run, '') <> $run
            DETACH DELETE s RETURN count(*) AS n
            """,
            uids=file_uids,
            run=run_uid,
        )
        return sum(r["n"] for r in rows)

    def card_hashes(self, model: str | None = None) -> dict[str, str]:
        """uid -> card hash of symbols already embedded (by `model`, when given)."""
        rows = self.store.read(
            "MATCH (s:Symbol {project_id: $p}) WHERE s.embedding IS NOT NULL "
            "AND ($model IS NULL OR s.embedding_model = $model) RETURN s.uid AS uid, s.card_hash AS h",
            p=self.project_id,
            model=model,
        )
        return {r["uid"]: r["h"] for r in rows}

    # -- edges -----------------------------------------------------------------

    def upsert_edges(
        self,
        rel_type: Rel | str,
        rows: Iterable[Mapping[str, Any]],
        run_uid: str | None = None,
        src_label: Label | str = Label.SYMBOL,
        dst_label: Label | str = Label.SYMBOL,
    ) -> int:
        """rows: {src, dst, props?}. Endpoints must already exist (MATCH, not MERGE)."""
        rt = rel(rel_type).value
        return self.store.write_batches(
            f"""
            UNWIND $rows AS row
            MATCH (a:{endpoint_label(src_label)} {{uid: row.src}})
            MATCH (b:{endpoint_label(dst_label)} {{uid: row.dst}})
            MERGE (a)-[r:{rt}]->(b)
            SET r += coalesce(row.props, {{}}), r.last_audit_run = $run
            """,
            rows,
            run=run_uid,
        )

    def delete_stale_edges(self, source_uids: list[str], run_uid: str, types: list[Rel] | None = None) -> int:
        """Remove auditor edges from re-audited sources that this run did not refresh."""
        if not source_uids:
            return 0
        rows = self.store.write(
            """
            UNWIND $uids AS uid
            MATCH (a {uid: uid})-[r]->()
            WHERE type(r) IN $types AND coalesce(r.last_audit_run, '') <> $run
            DELETE r
            RETURN count(r) AS deleted
            """,
            uids=source_uids,
            types=[r.value for r in (types or CODE_RELS)],
            run=run_uid,
        )
        return sum(r["deleted"] for r in rows)

    # -- deletion --------------------------------------------------------------

    def delete_file_subgraph(self, file_uids: list[str]) -> int:
        """Delete files and the symbols they define. Knowledge survives; evidence pointing
        at the deleted nodes is marked stale so Phase 4 revalidation re-checks it."""
        if not file_uids:
            return 0
        self.store.write(
            """
            UNWIND $uids AS uid
            MATCH (f:File {uid: uid})
            OPTIONAL MATCH (f)-[:DEFINES]->(s:Symbol)
            WITH collect(DISTINCT f) + collect(DISTINCT s) AS nodes
            UNWIND nodes AS n
            MATCH (e:Evidence)-[:REFERENCES]->(n)
            SET e.stale = true, e.stale_reason = 'target_deleted'
            """,
            uids=file_uids,
        )
        rows = self.store.write(
            """
            UNWIND $uids AS uid
            MATCH (f:File {uid: uid})
            OPTIONAL MATCH (f)-[:DEFINES]->(s:Symbol)
            WITH f, collect(s) AS symbols
            FOREACH (s IN symbols | DETACH DELETE s)
            DETACH DELETE f
            RETURN count(*) AS deleted
            """,
            uids=file_uids,
        )
        return sum(r["deleted"] for r in rows)

    def stale_files(self, run_uid: str) -> list[str]:
        rows = self.store.read(
            "MATCH (f:File {project_id: $p}) WHERE coalesce(f.last_audit_run, '') <> $run RETURN f.uid AS uid",
            p=self.project_id,
            run=run_uid,
        )
        return [r["uid"] for r in rows]

    def delete_empty_modules(self) -> int:
        rows = self.store.write(
            """
            MATCH (m:Module {project_id: $p})
            WHERE NOT (m)-[:CONTAINS*1..]->(:File)
            DETACH DELETE m
            RETURN count(*) AS deleted
            """,
            p=self.project_id,
        )
        return sum(r["deleted"] for r in rows)

    # -- reads -----------------------------------------------------------------

    def file_hashes(self) -> dict[str, str]:
        rows = self.store.read(
            "MATCH (f:File {project_id: $p}) RETURN f.path AS path, f.sha256 AS sha", p=self.project_id
        )
        return {r["path"]: r["sha"] for r in rows}

    def get_symbol(self, uid: str) -> dict[str, Any] | None:
        rows = self.store.read(
            "MATCH (s:Symbol {uid: $uid}) RETURN s {.*, embedding: null} AS s, labels(s) AS labels", uid=uid
        )
        if not rows:
            return None
        return {**rows[0]["s"], "labels": rows[0]["labels"]}

    def symbols_by_role(self, role_label: Role | str) -> list[dict[str, Any]]:
        r = role(role_label).value
        return self.store.read(
            f"MATCH (s:Symbol:{r} {{project_id: $p}}) RETURN s.uid AS uid, s.name AS name, s.path AS path "
            "ORDER BY s.path, s.name",
            p=self.project_id,
        )

    def symbols_in_files(self, paths: list[str]) -> list[dict[str, Any]]:
        return self.store.read(
            "MATCH (f:File {project_id: $p})-[:DEFINES]->(s:Symbol) WHERE f.path IN $paths "
            "RETURN s.uid AS uid, s.name AS name, s.path AS path, s.line_start AS line_start, "
            "s.line_end AS line_end, labels(s) AS labels",
            p=self.project_id,
            paths=paths,
        )
