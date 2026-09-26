"""Pattern detectors: repeated structure -> evidence-backed pattern candidates (spec §9.3, §14).

Each detector returns support (symbols that follow the pattern) and violations
(counter-examples). Confidence is computed from those counts in memory/confidence.py,
so one violation visibly lowers it instead of being ignored.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any

from .graphview import GraphView

LIFECYCLES: dict[str, dict[str, set[str]]] = {
    "token": {
        "create": {"create", "issue", "generate", "mint", "sign"},
        "validate": {"validate", "verify", "check", "find", "lookup"},
        "consume": {"consume", "redeem", "use", "burn"},
        "expire": {"expire", "revoke", "invalidate", "purge", "cleanup"},
    },
    "queue": {
        "enqueue": {"enqueue", "add", "push", "publish", "schedule"},
        "process": {"process", "handle", "consume", "run"},
        "retry": {"retry", "requeue", "fail", "dead"},
    },
}
VALIDATION_LIBS = ("zod", "joi", "yup", "class-validator", "valibot", "pydantic", "marshmallow")
TEST_FRAMEWORKS = ("vitest", "jest", "mocha", "pytest", "ava")
BODY_METHODS = {"POST", "PUT", "PATCH"}


@dataclass
class PatternCandidate:
    key: str
    title: str
    category: str
    claim: str
    detector: str
    support: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


def layered_flow(view: GraphView) -> list[PatternCandidate]:
    """Controllers delegate to services; only repositories touch the database."""
    out = []
    controllers = view.with_role("Controller")
    if controllers:
        support, violations = [], []
        for c in controllers:
            deps = view.deps(c.uid, ("USES", "CALLS", "ACCESSES"))
            bad = [d for d in deps if view.roles(d) & {"Repository", "Model"}]
            if bad:
                violations.append(c.uid)
            elif any("Service" in view.roles(d) for d in deps):
                support.append(c.uid)
        out.append(
            PatternCandidate(
                "controllers-delegate-to-services",
                "Controllers delegate to services",
                "service",
                "Controllers translate HTTP into service calls and never reach repositories or the database directly.",
                "layered_flow",
                support,
                violations,
            )
        )
    accessors = {view.owner(src) for src in view.out["ACCESSES"]}
    if accessors:
        support = sorted(a for a in accessors if "Repository" in view.roles(a))
        violations = sorted(a for a in accessors if "Repository" not in view.roles(a))
        out.append(
            PatternCandidate(
                "data-access-through-repositories",
                "Data access goes through repositories",
                "repository",
                "Only repositories read or write database tables; services and controllers call repositories.",
                "layered_flow",
                support,
                violations,
            )
        )
    return out


def naming_suffix(view: GraphView) -> list[PatternCandidate]:
    out = []
    for role, suffix in (("Service", "Service"), ("Repository", "Repository"), ("Controller", "Controller")):
        syms = [s for s in view.with_role(role) if s.kind == "class"]
        if len(syms) < 2:
            continue
        dirs = Counter(str(PurePosixPath(s.path).parent) for s in syms)
        main_dir, _ = dirs.most_common(1)[0]
        file_suffix = Counter(".".join(PurePosixPath(s.path).name.split(".")[1:]) for s in syms).most_common(1)[0][0]
        support = [s.uid for s in syms if s.name.endswith(suffix) and str(PurePosixPath(s.path).parent) == main_dir]
        violations = [s.uid for s in syms if s.uid not in support]
        glob = f"{main_dir}/*.{file_suffix}" if file_suffix else f"{main_dir}/*"
        out.append(
            PatternCandidate(
                f"naming-{role.lower()}",
                f"{role} classes live in {main_dir}/ and end with '{suffix}'",
                "naming",
                f"{role} classes are named *{suffix} and placed in {glob}.",
                "naming_suffix",
                support,
                violations,
                {"role": role, "dir": main_dir, "glob": glob, "suffix": suffix},
            )
        )
    return out


def validation_lib(view: GraphView) -> list[PatternCandidate]:
    validators = view.with_role("Validator")
    libs = Counter(str(v.props.get("validator")) for v in validators if v.props.get("validator"))
    if not libs:
        return []
    lib = libs.most_common(1)[0][0]
    routes = [r for r in view.with_role("Route") if str(r.props.get("http_method", "")).upper() in BODY_METHODS]
    if not routes:
        return []
    validator_uids = {v.uid for v in validators}
    support: list[str] = []
    violations: list[str] = []
    for r in routes:
        uses_validator = bool(view.out["USES"].get(r.uid, set()) & validator_uids) or r.props.get("validated")
        (support if uses_validator else violations).append(r.uid)
    return [
        PatternCandidate(
            "route-input-validation",
            f"Route input is validated with {lib.capitalize()} schemas",
            "validation",
            f"Routes that accept a body (POST/PUT/PATCH) validate it with a {lib} schema before the handler runs.",
            "validation_lib",
            support,
            violations,
            {"library": lib},
        )
    ]


def error_handling(view: GraphView) -> list[PatternCandidate]:
    extends = view.out["EXTENDS"]
    parents = Counter(dst for dsts in extends.values() for dst in dsts)
    if not parents:
        return []
    base, count = parents.most_common(1)[0]
    if count < 2 or base not in view.symbols:
        return []
    base_sym = view.symbols[base]
    error_like = [
        s
        for s in view.symbols.values()
        if s.kind == "class" and s.name.endswith(("Error", "Exception")) and s.uid != base
    ]
    support = sorted(s.uid for s in error_like if base in extends.get(s.uid, set()))
    violations = sorted(s.uid for s in error_like if base not in extends.get(s.uid, set()))
    return [
        PatternCandidate(
            "errors-extend-base",
            f"Errors extend {base_sym.name}",
            "error",
            f"Application errors subclass {base_sym.name} ({base_sym.path}) so one handler maps them to responses.",
            "error_handling",
            support,
            violations,
            {"base": base},
        )
    ]


def test_layout(view: GraphView) -> list[PatternCandidate]:
    tests = {p: f for p, f in view.files.items() if f["is_test"]}
    if len(tests) < 2:
        return []
    layouts = Counter("tests-dir" if p.split("/")[0] in ("tests", "test", "__tests__") else "co-located" for p in tests)
    layout = layouts.most_common(1)[0][0]
    frameworks = Counter(x for f in tests.values() for x in f["external_imports"] if x in TEST_FRAMEWORKS)
    framework = frameworks.most_common(1)[0][0] if frameworks else "the project test runner"
    support = sorted(
        f["uid"]
        for p, f in tests.items()
        if (p.split("/")[0] in ("tests", "test", "__tests__")) == (layout == "tests-dir")
    )
    violations = sorted(f["uid"] for f in tests.values() if f["uid"] not in support)
    where = "in tests/" if layout == "tests-dir" else "next to the code they test"
    return [
        PatternCandidate(
            "test-layout",
            f"Tests live {where} and use {framework}",
            "testing",
            f"Test files live {where} and are written with {framework}.",
            "test_layout",
            support,
            violations,
            {"layout": layout, "framework": framework},
        )
    ]


def lifecycle_verbs(view: GraphView) -> list[PatternCandidate]:
    """A class that owns a lifecycle (e.g. tokens: create/validate/consume/expire) and the classes reusing it."""
    out = []
    for name, stages in LIFECYCLES.items():
        for sym in (s for s in view.symbols.values() if s.kind == "class"):
            covered = [
                stage for stage, verbs in stages.items() if any(m.lower().startswith(tuple(verbs)) for m in sym.methods)
            ]
            if len(covered) < 3:
                continue
            users = sorted(u for u in view.users(sym.uid) if view.symbols.get(u) and view.symbols[u].kind == "class")
            # Classes that re-implement two or more stages themselves instead of using the owner.
            rivals = []
            for other in (s for s in view.symbols.values() if s.kind == "class" and s.uid != sym.uid):
                own = [
                    st for st, verbs in stages.items() if any(m.lower().startswith(tuple(verbs)) for m in other.methods)
                ]
                if len(own) >= 2 and sym.uid not in view.deps(other.uid) and name in other.name.lower():
                    rivals.append(other.uid)
            flow = " → ".join(covered)
            out.append(
                PatternCandidate(
                    f"lifecycle-{name}-{sym.name.lower()}",
                    f"{name.capitalize()} lifecycle is owned by {sym.name}",
                    "lifecycle",
                    f"{sym.name} owns the {name} lifecycle ({flow}); features needing it reuse {sym.name} "
                    f"instead of adding a new {name} store.",
                    "lifecycle_verbs",
                    [sym.uid, *users],
                    rivals,
                    {"owner": sym.uid, "stages": covered, "flow": flow},
                )
            )
    return out


def di_style(view: GraphView) -> list[PatternCandidate]:
    roles = ("Service", "Controller", "Repository")
    classes = [s for r in roles for s in view.with_role(r) if s.kind == "class"]
    if len(classes) < 3:
        return []
    support: list[str] = []
    violations: list[str] = []
    for c in classes:
        edges = [
            (dst, view.edge_props.get(("USES", c.uid, dst), {}).get("via"))
            for dst in view.out["USES"].get(c.uid, set())
        ]
        project_deps = [(d, via) for d, via in edges if view.roles(d) & {"Service", "Repository", "Integration"}]
        if any(via == "new" for _, via in project_deps):
            violations.append(c.uid)
        elif any(via in ("constructor", "field") for _, via in project_deps):
            support.append(c.uid)
    if not support:
        return []
    return [
        PatternCandidate(
            "constructor-injection",
            "Dependencies are injected through constructors",
            "service",
            "Services, controllers and repositories receive their collaborators as constructor parameters; "
            "only the composition root creates instances.",
            "di_style",
            sorted(support),
            sorted(violations),
        )
    ]


DETECTORS = (layered_flow, naming_suffix, validation_lib, error_handling, test_layout, lifecycle_verbs, di_style)


def detect_patterns(view: GraphView) -> list[PatternCandidate]:
    found: list[PatternCandidate] = []
    for detector in DETECTORS:
        found.extend(detector(view))
    return [p for p in found if p.support or p.violations]
