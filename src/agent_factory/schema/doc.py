"""Generate docs/graph-schema.md from the live database (labels, relationships, indexes)."""

from __future__ import annotations

from ..db.migrations import constraint_names, current_version, index_states
from ..db.repos import AdminRepo
from ..db.stores import Neo4jStore
from .model import Label, Rel, Role

_LABEL_NOTES = {
    Label.PROJECT: "one per configured project",
    Label.MODULE: "package boundary (package.json, __init__.py, top-level source dir)",
    Label.FILE: "source file; `:TestFile` for tests",
    Label.SYMBOL: "class / function / method / route / model; role labels added",
    Label.KNOWLEDGE: "super-label of Pattern, Decision, Constraint",
    Label.PATTERN: "repeated implementation pattern",
    Label.DECISION: "ADR or inferred architectural decision",
    Label.CONSTRAINT: "rule future code must not violate",
    Label.FEATURE: "feature memory: what a feature changed, reused, proved",
    Label.EVIDENCE: "what backs a claim: code refs, test runs, screenshots",
    Label.COMMIT: "git history (author hashed)",
    Label.DOC: "README / ADR / guide",
    Label.DOC_CHUNK: "embedded documentation chunk",
    Label.AUDIT_RUN: "one audit execution",
    Label.MEMORY_EVENT: "audit log entry for a knowledge change",
    Label.SCHEMA_VERSION: "applied migrations",
}


def schema_markdown(store: Neo4jStore, project_id: str | None = None) -> str:
    counts = AdminRepo(store, project_id).stats() if project_id else {"labels": {}, "relationships": {}}
    lines = [
        "# Agent Factory graph schema",
        "",
        f"Schema version **{current_version(store)}**"
        + (f" · counts for project `{project_id}`" if project_id else ""),
        "",
        "## Node labels",
        "",
        "| Label | Count | Notes |",
        "|---|---|---|",
    ]
    for lbl in Label:
        lines.append(f"| `{lbl.value}` | {counts['labels'].get(lbl.value, 0)} | {_LABEL_NOTES.get(lbl, '')} |")
    lines += ["", "## Role labels (on `:Symbol`)", "", "| Role | Count |", "|---|---|"]
    for r in Role:
        lines.append(f"| `{r.value}` | {counts['labels'].get(r.value, 0)} |")
    lines += ["", "## Relationships", "", "| Type | Count |", "|---|---|"]
    for rt in Rel:
        lines.append(f"| `{rt.value}` | {counts['relationships'].get(rt.value, 0)} |")
    lines += ["", "## Indexes", "", "| Name | Type | Labels | Properties | State |", "|---|---|---|---|---|"]
    for s in index_states(store):
        dims = f" ({s.dimensions}-d)" if s.dimensions else ""
        lines.append(f"| `{s.name}` | {s.type}{dims} | {', '.join(s.labels)} | {', '.join(s.properties)} | {s.state} |")
    lines += ["", "## Constraints", ""]
    lines += [f"- `{name}`" for name in constraint_names(store)]
    return "\n".join(lines) + "\n"
