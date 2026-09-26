"""Impact analysis: "if I change X, what is affected?" (spec §8.1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..db.stores import Neo4jStore

UPSTREAM = """
MATCH (t:Symbol {uid: $uid})
OPTIONAL MATCH (t)-[:HAS_MEMBER]->(tm:Symbol)
WITH t, [t] + collect(tm) AS targets
UNWIND targets AS x
MATCH p = (d:Symbol)-[:USES|CALLS|EXTENDS|IMPLEMENTS|HANDLES*1..%d]->(x)
WHERE d.project_id = $p AND NOT d IN targets AND d.kind <> 'const'
OPTIONAL MATCH (o:Symbol)-[:HAS_MEMBER]->(d)
WITH coalesce(o, d) AS dep, min(length(p)) AS hops, t
WHERE dep.uid <> t.uid
RETURN dep.uid AS uid, dep.name AS name, dep.kind AS kind, coalesce(dep.roles, []) AS roles, dep.path AS path,
       dep.line_start AS line, min(hops) AS hops, dep.http_method AS http_method, dep.http_path AS http_path
ORDER BY hops, name
"""

DOWNSTREAM = """
MATCH (t:Symbol {uid: $uid})-[:HAS_MEMBER*0..1]->()-[:USES|CALLS|ACCESSES|EXTENDS]->(x:Symbol)
OPTIONAL MATCH (o:Symbol)-[:HAS_MEMBER]->(x)
WITH t, coalesce(o, x) AS dep WHERE dep.uid <> t.uid AND dep.kind <> 'const'
RETURN DISTINCT dep.uid AS uid, dep.name AS name, coalesce(dep.roles, []) AS roles, dep.path AS path
ORDER BY name
"""

CONTEXT = """
MATCH (t:Symbol {uid: $uid})
OPTIONAL MATCH (tf:TestFile)-[:COVERS]->(t)
OPTIONAL MATCH (t)<-[:DEFINES]-(:File)-[cc:CO_CHANGES]-(g:File)
OPTIONAL MATCH (t)-[:CONSTRAINED_BY|FOLLOWS]->(k:Knowledge) WHERE k.status = 'validated'
OPTIONAL MATCH (f:Feature)-[:MODIFIES|REUSES|INTRODUCES]->(t)
RETURN collect(DISTINCT tf.path) AS tests, collect(DISTINCT {path: g.path, count: cc.count}) AS co_changes,
       collect(DISTINCT {uid: k.uid, kind: k.kind, title: k.title}) AS knowledge,
       collect(DISTINCT {uid: f.uid, name: f.name}) AS features
"""


class ImpactEntry(BaseModel):
    uid: str
    name: str
    kind: str | None = None
    role: str | None = None
    path: str | None = None
    line: int | None = None
    hops: int | None = None


class ImpactReport(BaseModel):
    target: ImpactEntry
    depth: int
    dependents: list[ImpactEntry] = Field(default_factory=list)
    dependencies: list[ImpactEntry] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)
    co_changes: list[dict[str, Any]] = Field(default_factory=list)
    knowledge: list[dict[str, Any]] = Field(default_factory=list)
    features: list[dict[str, Any]] = Field(default_factory=list)
    candidates: list[ImpactEntry] = Field(default_factory=list)  # when the target name was ambiguous


class TargetNotFound(LookupError):
    pass


def resolve_target(store: Neo4jStore, project_id: str, ref: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """A uid, a qualified name (Class.method), a symbol name, or a file path. -> (target, other matches)."""
    rows = store.read(
        """
        MATCH (s:Symbol {project_id: $p})
        WHERE s.uid = $ref OR s.qualname = $ref OR s.name = $ref OR s.path = $ref
        RETURN s.uid AS uid, s.name AS name, s.kind AS kind, coalesce(s.roles, []) AS roles, s.path AS path,
               s.line_start AS line, s.parent IS NULL AS top, s.uid = $ref OR s.qualname = $ref AS exact
        """,
        p=project_id,
        ref=ref,
    )
    if not rows:
        raise TargetNotFound(f"no symbol or file matches {ref!r}")
    rank = {"class": 0, "function": 1, "route": 2, "const": 3, "method": 4}
    rows.sort(key=lambda r: (not r["exact"], not r["top"], rank.get(r["kind"], 9), r["path"], r["name"]))
    return rows[0], rows[1:]


def analyze(store: Neo4jStore, project_id: str, ref: str, depth: int = 2) -> ImpactReport:
    depth = max(1, min(4, depth))
    target, others = resolve_target(store, project_id, ref)
    uid = target["uid"]
    up = store.read(UPSTREAM % depth, uid=uid, p=project_id)
    down = store.read(DOWNSTREAM, uid=uid)
    ctx = store.read(CONTEXT, uid=uid)[0]
    dependent_uids = [r["uid"] for r in up]
    tests = set(ctx["tests"])
    if dependent_uids:
        tests |= {r["path"] for r in store.read(
            "UNWIND $u AS uid MATCH (tf:TestFile)-[:COVERS]->(:Symbol {uid: uid}) RETURN DISTINCT tf.path AS path",
            u=dependent_uids)}

    def entry(r: dict[str, Any]) -> ImpactEntry:
        return ImpactEntry(uid=r["uid"], name=r["name"], kind=r.get("kind"), role=(r.get("roles") or [None])[0],
                           path=r.get("path"), line=r.get("line"), hops=r.get("hops"))

    return ImpactReport(
        target=entry(target),
        depth=depth,
        dependents=[entry(r) for r in up if r["kind"] != "route"],
        dependencies=[entry(r) for r in down],
        routes=sorted({f"{r['http_method']} {r['http_path']}" for r in up if r["kind"] == "route"}),
        tests=sorted(tests),
        co_changes=sorted((c for c in ctx["co_changes"] if c.get("path")), key=lambda c: -(c["count"] or 0)),
        knowledge=[k for k in ctx["knowledge"] if k.get("uid")],
        features=[f for f in ctx["features"] if f.get("uid")],
        candidates=[entry(r) for r in others[:5]],
    )


def render_impact(rep: ImpactReport) -> str:
    t = rep.target
    lines = [f"Impact of changing {t.name} ({t.role or t.kind}) — {t.path}:{t.line}", ""]
    if rep.dependents:
        lines.append(f"Dependents (up to {rep.depth} hops):")
        lines += [f"  {'  ' * ((d.hops or 1) - 1)}← {d.name} ({d.role or d.kind}) {d.path}" for d in rep.dependents]
    else:
        lines.append("Nothing in the graph depends on it.")
    if rep.routes:
        lines.append("Routes reaching it: " + ", ".join(rep.routes))
    if rep.tests:
        lines.append("Tests to run: " + ", ".join(rep.tests))
    if rep.dependencies:
        lines.append("It depends on: " + ", ".join(d.name for d in rep.dependencies))
    if rep.co_changes:
        lines.append("Usually changes together with: " + ", ".join(f"{c['path']} ({c['count']}×)" for c in rep.co_changes[:5]))
    if rep.knowledge:
        lines.append("Rules and patterns in force: " + "; ".join(k["title"] for k in rep.knowledge))
    if rep.features:
        lines.append("Past features that touched it: " + ", ".join(f["name"] for f in rep.features))
    if rep.candidates:
        lines.append("Other matches for this name: " + ", ".join(f"{c.name} ({c.path})" for c in rep.candidates))
    return "\n".join(lines)
