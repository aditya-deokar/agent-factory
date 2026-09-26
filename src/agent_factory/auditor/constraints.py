"""Machine-checkable constraints (spec §9.7), inferred from strong patterns and external deps.

Every rule has two evaluators that must agree: a Python one over a GraphView
(used for re-derivation and, in Phase 8, for diffs) and a Cypher one stored on
the Constraint node for whole-graph checks in Neo4j Browser or via MCP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .graphview import GraphView
from .patterns import PatternCandidate
from .walker import glob_to_regex

MIN_SUPPORT_FOR_RULE = 3
DEP_CATEGORIES: dict[str, tuple[str, ...]] = {
    "validation": ("zod", "joi", "yup", "class-validator", "valibot", "pydantic", "marshmallow"),
    "orm": (
        "drizzle-orm",
        "prisma",
        "@prisma/client",
        "typeorm",
        "sequelize",
        "mongoose",
        "knex",
        "sqlalchemy",
        "django",
    ),
    "state-management": ("redux", "@reduxjs/toolkit", "zustand", "mobx", "jotai", "recoil", "pinia", "vuex"),
    "http-client": ("axios", "got", "node-fetch", "ky", "superagent", "requests", "httpx"),
    "queue": ("bullmq", "bull", "bee-queue", "agenda", "celery", "rq"),
    "test": ("vitest", "jest", "mocha", "ava", "pytest"),
    "email": ("nodemailer", "@sendgrid/mail", "postmark", "resend"),
    "web-framework": ("express", "fastify", "koa", "@nestjs/core", "hono", "fastapi", "flask"),
}
RULE_TYPES = ("forbid_dependency", "restrict_access", "forbid_external_dep", "placement")


@dataclass
class ConstraintCandidate:
    key: str
    title: str
    claim: str
    rule_type: str
    rule: dict[str, Any]
    severity: str = "error"
    derived_from: str | None = None  # pattern key
    support: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    @property
    def check_cypher(self) -> str | None:
        return check_cypher(self.rule_type, self.rule)


def check_cypher(rule_type: str, rule: dict[str, Any]) -> str | None:
    """Read-only Cypher returning (violator, target) rows for a rule, parameterised by $p (project id)."""
    if rule_type == "forbid_dependency":
        return (
            "MATCH (c:Symbol {project_id: $p}) WHERE $from_role IN c.roles "
            "MATCH (c)-[:HAS_MEMBER*0..1]->(x)-[:USES|CALLS|ACCESSES]->(t:Symbol) "
            "OPTIONAL MATCH (owner:Symbol)-[:HAS_MEMBER]->(t) "
            "WITH c, coalesce(owner, t) AS target WHERE any(r IN target.roles WHERE r IN $to_roles) "
            "RETURN DISTINCT c.uid AS violator, target.uid AS target"
        )
    if rule_type == "restrict_access":
        return (
            "MATCH (x:Symbol {project_id: $p})-[:ACCESSES]->(m:Symbol) "
            "OPTIONAL MATCH (owner:Symbol)-[:HAS_MEMBER]->(x) "
            "WITH coalesce(owner, x) AS accessor, m WHERE NOT any(r IN accessor.roles WHERE r IN $allowed_roles) "
            "RETURN DISTINCT accessor.uid AS violator, m.uid AS target"
        )
    return None


def cypher_params(rule_type: str, rule: dict[str, Any]) -> dict[str, Any]:
    if rule_type == "forbid_dependency":
        return {"from_role": rule["from_role"], "to_roles": list(rule["to_roles"])}
    if rule_type == "restrict_access":
        return {"allowed_roles": list(rule["allowed_roles"])}
    return {}


def evaluate(view: GraphView, rule_type: str, rule: dict[str, Any]) -> tuple[list[str], list[tuple[str, str]]]:
    """-> (in-scope symbols that comply, [(violator, target)])."""
    if rule_type == "forbid_dependency":
        scope = [s for s in view.with_role(rule["from_role"])]
        violations = []
        for s in scope:
            for d in sorted(view.deps(s.uid, ("USES", "CALLS", "ACCESSES"))):
                if view.roles(d) & set(rule["to_roles"]):
                    violations.append((s.uid, d))
        bad = {v for v, _ in violations}
        return [s.uid for s in scope if s.uid not in bad], violations
    if rule_type == "restrict_access":
        allowed = set(rule["allowed_roles"])
        ok, violations = set(), []
        for src, dsts in view.out["ACCESSES"].items():
            accessor = view.owner(src)
            for m in sorted(dsts):
                if view.roles(accessor) & allowed:
                    ok.add(accessor)
                else:
                    violations.append((accessor, m))
        return sorted(ok - {v for v, _ in violations}), sorted(set(violations))
    if rule_type == "forbid_external_dep":
        allowed = set(rule["allowed"])
        category = DEP_CATEGORIES.get(rule["category"], ())
        users, violations = [], []
        for f in view.files.values():
            used = set(f["external_imports"]) & set(category)
            if used & allowed:
                users.append(f["uid"])
            for lib in sorted(used - allowed):
                violations.append((f["uid"], lib))
        for dep in sorted(set(view.external_deps) & set(category) - allowed):
            violations.append(("manifest", dep))
        return sorted(users), violations
    if rule_type == "placement":
        pattern = glob_to_regex(rule["glob"])
        scope = [s for s in view.with_role(rule["role"]) if s.kind == "class"]
        placed = [s.uid for s in scope if pattern.match(s.path)]
        return placed, [(s.uid, s.path) for s in scope if s.uid not in placed]
    raise ValueError(f"unknown rule type {rule_type!r}")


def infer_constraints(view: GraphView, patterns: list[PatternCandidate]) -> list[ConstraintCandidate]:
    out: list[ConstraintCandidate] = []
    by_key = {p.key: p for p in patterns}
    ctl = by_key.get("controllers-delegate-to-services")
    if ctl and len(ctl.support) + len(ctl.violations) >= MIN_SUPPORT_FOR_RULE:
        out.append(
            ConstraintCandidate(
                "forbid-dependency-controller-repository",
                "Controllers must not use repositories or models directly",
                "A Controller (or any of its methods) must not use, call or access a Repository or Model; go through a "
                "service.",
                "forbid_dependency",
                {"from_role": "Controller", "to_roles": ["Repository", "Model"]},
                derived_from=ctl.key,
            )
        )
    access = by_key.get("data-access-through-repositories")
    if access and len(access.support) >= MIN_SUPPORT_FOR_RULE:
        out.append(
            ConstraintCandidate(
                "restrict-access-models-repositories",
                "Only repositories access database tables",
                "Reading or writing a Model (table) is allowed only from Repository classes.",
                "restrict_access",
                {"allowed_roles": ["Repository"]},
                derived_from=access.key,
            )
        )
    for p in patterns:
        if p.detector == "naming_suffix" and len(p.support) >= MIN_SUPPORT_FOR_RULE:
            role, glob = p.extra["role"], p.extra["glob"]
            out.append(
                ConstraintCandidate(
                    f"placement-{role.lower()}",
                    f"New {role} classes go in {PurePosixPath(glob).parent}/",
                    f"{role} classes are placed in {glob}.",
                    "placement",
                    {"role": role, "glob": glob},
                    severity="warn",
                    derived_from=p.key,
                )
            )
    used: dict[str, set[str]] = {}
    for f in view.files.values():
        for lib in f["external_imports"]:
            for category, libs in DEP_CATEGORIES.items():
                if lib in libs:
                    used.setdefault(category, set()).add(lib)
    for dep in view.external_deps:
        for category, libs in DEP_CATEGORIES.items():
            if dep in libs:
                used.setdefault(category, set()).add(dep)
    for category, found in sorted(used.items()):
        if len(found) != 1 or category == "test":
            continue
        lib = next(iter(found))
        out.append(
            ConstraintCandidate(
                f"single-{category}-library",
                f"Use {lib} for {category.replace('-', ' ')}; do not add another library",
                f"The project standardises on {lib} for {category.replace('-', ' ')}. Adding a second "
                f"{category.replace('-', ' ')} library needs an explicit decision.",
                "forbid_external_dep",
                {"category": category, "allowed": [lib]},
                severity="warn",
            )
        )
    for c in out:
        ok, violations = evaluate(view, c.rule_type, c.rule)
        c.support = ok
        c.violations = [v for v, _ in violations]
    return out
