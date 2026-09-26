"""An in-memory view of one project's code graph for pattern detection and rule checks.

Detectors and constraint evaluators run on this view, loaded from Neo4j after the
code graph is written (so incremental audits still see the whole project) or
built straight from a GraphFragment (dry runs, diffs, unit tests).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..db.stores import Neo4jStore
from .model import GraphFragment

EDGE_TYPES = ("USES", "CALLS", "ACCESSES", "HANDLES", "EXTENDS", "IMPLEMENTS", "HAS_MEMBER", "COVERS", "IMPORTS")


@dataclass
class Sym:
    uid: str
    name: str
    kind: str
    path: str
    roles: set[str]
    parent_uid: str | None = None
    methods: list[str] = field(default_factory=list)
    line_start: int = 1
    line_end: int = 1
    exported: bool = False
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphView:
    project_id: str
    symbols: dict[str, Sym] = field(default_factory=dict)
    files: dict[str, dict[str, Any]] = field(default_factory=dict)  # path -> {is_test, external_imports, uid}
    out: dict[str, dict[str, set[str]]] = field(default_factory=lambda: defaultdict(lambda: defaultdict(set)))
    edge_props: dict[tuple[str, str, str], dict[str, Any]] = field(default_factory=dict)
    external_deps: dict[str, str] = field(default_factory=dict)

    # -- construction -------------------------------------------------------------

    def add_edge(self, rel: str, src: str, dst: str, props: dict[str, Any] | None = None) -> None:
        self.out[rel][src].add(dst)
        if props:
            self.edge_props[(rel, src, dst)] = props

    @classmethod
    def from_fragment(cls, frag: GraphFragment) -> GraphView:
        view = cls(frag.project_id, external_deps=dict(frag.external_deps))
        parents = {
            row.uid: f"{row.uid.split('#', 1)[0]}#{row.props['parent']}"
            for row in frag.symbols
            if row.props.get("parent")
        }
        for row in frag.symbols:
            p = row.props
            view.symbols[row.uid] = Sym(
                row.uid,
                p["name"],
                p["kind"],
                p["path"],
                set(row.roles),
                parents.get(row.uid),
                list(p.get("methods") or []),
                p.get("line_start", 1),
                p.get("line_end", 1),
                bool(p.get("exported")),
                dict(p),
            )
        for f in frag.files:
            view.files[f["path"]] = {
                "uid": f["uid"],
                "is_test": f["is_test"],
                "external_imports": list(f.get("external_imports") or []),
            }
        for e in frag.edges:
            if e.rel in EDGE_TYPES:
                view.add_edge(e.rel, e.src, e.dst, e.props)
        return view

    @classmethod
    def load(cls, store: Neo4jStore, project_id: str) -> GraphView:
        view = cls(project_id)
        rows = store.read(
            """
            MATCH (s:Symbol {project_id: $p})
            OPTIONAL MATCH (c:Symbol)-[:HAS_MEMBER]->(s)
            RETURN s.uid AS uid, s.name AS name, s.kind AS kind, s.path AS path, coalesce(s.roles, []) AS roles,
                   c.uid AS parent_uid, coalesce(s.methods, []) AS methods, s.line_start AS line_start,
                   s.line_end AS line_end, coalesce(s.exported, false) AS exported,
                   s {.http_method, .http_path, .validated, .table, .validator, .signature, .doc, .loc} AS props
            """,
            p=project_id,
        )
        for r in rows:
            view.symbols[r["uid"]] = Sym(
                r["uid"],
                r["name"],
                r["kind"],
                r["path"],
                set(r["roles"]),
                r["parent_uid"],
                list(r["methods"]),
                r["line_start"] or 1,
                r["line_end"] or 1,
                r["exported"],
                {k: v for k, v in (r["props"] or {}).items() if v is not None},
            )
        for r in store.read(
            "MATCH (f:File {project_id: $p}) RETURN f.uid AS uid, f.path AS path, f:TestFile AS is_test, "
            "coalesce(f.external_imports, []) AS ext",
            p=project_id,
        ):
            view.files[r["path"]] = {"uid": r["uid"], "is_test": r["is_test"], "external_imports": list(r["ext"])}
        for r in store.read(
            """
            MATCH (a {project_id: $p})-[r]->(b)
            WHERE type(r) IN $types AND (a:Symbol OR a:File)
            RETURN type(r) AS rel, a.uid AS src, b.uid AS dst, properties(r) AS props
            """,
            p=project_id,
            types=list(EDGE_TYPES),
        ):
            props = {k: v for k, v in (r["props"] or {}).items() if k != "last_audit_run"}
            view.add_edge(r["rel"], r["src"], r["dst"], props)
        proj = store.read("MATCH (p:Project {id: $p}) RETURN p.external_deps AS deps", p=project_id)
        if proj and proj[0]["deps"]:
            view.external_deps = {d: "manifest" for d in proj[0]["deps"]}
        return view

    # -- queries ------------------------------------------------------------------

    def owner(self, uid: str) -> str:
        sym = self.symbols.get(uid)
        return sym.parent_uid if sym is not None and sym.parent_uid else uid

    def roles(self, uid: str) -> set[str]:
        sym = self.symbols.get(self.owner(uid))
        return sym.roles if sym else set()

    def with_role(self, role: str) -> list[Sym]:
        return sorted((s for s in self.symbols.values() if role in s.roles), key=lambda s: (s.path, s.name))

    def members(self, uid: str) -> list[str]:
        return sorted(self.out["HAS_MEMBER"].get(uid, set()))

    def deps(self, uid: str, rels: tuple[str, ...] = ("USES", "CALLS")) -> set[str]:
        """Owner-level targets reached from a symbol or any of its members."""
        sources = [uid, *self.members(uid)]
        found: set[str] = set()
        for src in sources:
            for rel in rels:
                found |= {self.owner(t) for t in self.out[rel].get(src, set())}
        found.discard(uid)
        return found

    def direct_targets(self, uid: str, rel: str) -> list[tuple[str, str]]:
        """(source member-or-self, target) pairs for one relationship, with member granularity."""
        pairs = []
        for src in [uid, *self.members(uid)]:
            pairs += [(src, t) for t in sorted(self.out[rel].get(src, set()))]
        return pairs

    def users(self, uid: str, rels: tuple[str, ...] = ("USES", "CALLS")) -> set[str]:
        targets = {uid, *self.members(uid)}
        found: set[str] = set()
        for rel in rels:
            for src, dsts in self.out[rel].items():
                if dsts & targets:
                    found.add(self.owner(src))
        found.discard(uid)
        return found

    def file_of(self, uid: str) -> str:
        sym = self.symbols.get(uid)
        return sym.path if sym else uid.split(":file:", 1)[-1]
