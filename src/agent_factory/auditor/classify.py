"""Architectural role classification: weighted signals, kept when the score reaches 0.6.

Local signals (name, path, decorators, framework hints) come first; graph signals
(used by a controller, accesses a model) are added after references are resolved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..schema.model import Role
from .model import SymbolDef

THRESHOLD = 0.6
_HOOK = re.compile(r"^use[A-Z]\w*$")
_PASCAL = re.compile(r"^[A-Z][A-Za-z0-9]*$")


@dataclass(frozen=True)
class Rule:
    role: Role
    suffixes: tuple[str, ...] = ()
    dirs: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    suffix_weight: float = 0.6
    dir_weight: float = 0.3
    decorator_weight: float = 0.4


RULES: tuple[Rule, ...] = (
    Rule(Role.SERVICE, ("Service",), ("services", "service"), ("Injectable", "Service")),
    Rule(Role.REPOSITORY, ("Repository", "Repo"), ("repositories", "repository", "repos", "dao"), ("Repository",)),
    Rule(Role.CONTROLLER, ("Controller",), ("controllers", "controller"), ("Controller", "RestController")),
    Rule(Role.MIDDLEWARE, ("Middleware",), ("middleware", "middlewares"), (), 0.5, 0.4),
    Rule(Role.WORKER, ("Worker", "Processor", "Job", "Consumer"), ("workers", "jobs", "processors"), ("Processor",)),
    Rule(Role.QUEUE, ("Queue", "Producer", "Publisher"), ("queues",), ()),
    Rule(Role.VALIDATOR, ("Validator", "Schema"), ("validators", "schemas", "validation"), ()),
    Rule(
        Role.INTEGRATION,
        ("Client", "Gateway", "Adapter", "Integration"),
        ("integrations", "clients", "adapters"),
        (),
        0.3,
        0.4,
    ),
)


def _dirs(path: str) -> list[str]:
    return [p.lower() for p in path.split("/")[:-1]]


def local_scores(sym: SymbolDef, path: str, external_imports: set[str] | None = None) -> dict[Role, float]:
    """Scores from the symbol itself. Methods never get roles (their class does)."""
    scores: dict[Role, float] = {}
    if sym.parent is not None or sym.kind == "method":
        return scores
    if sym.kind == "route":
        return {Role.ROUTE: 1.0}
    if sym.hints.get("table"):
        scores[Role.MODEL] = 0.95
    if sym.hints.get("validator"):
        scores[Role.VALIDATOR] = 0.8
    if sym.kind == "const":
        # Constants (instances, config objects) only get roles from framework hints, never from names.
        return scores
    dirs = _dirs(path)
    file_name = path.rsplit("/", 1)[-1].lower()
    for rule in RULES:
        s = 0.0
        if any(sym.name.endswith(suf) for suf in rule.suffixes):
            s += rule.suffix_weight
        if any(d in dirs for d in rule.dirs) or any(f".{d.rstrip('s')}." in file_name for d in rule.dirs):
            s += rule.dir_weight
        if any(d in rule.decorators for d in sym.decorators):
            s += rule.decorator_weight
        if s:
            scores[rule.role] = max(scores.get(rule.role, 0.0), s)
    if sym.kind == "function" and _HOOK.match(sym.name) and path.endswith((".ts", ".tsx", ".js", ".jsx")):
        scores[Role.HOOK] = 0.8
    if sym.kind in ("function", "class") and sym.returns_jsx and _PASCAL.match(sym.name):
        scores[Role.COMPONENT] = 0.85
    if sym.kind == "function" and "next" in sym.signature and "req" in sym.signature:
        scores[Role.MIDDLEWARE] = scores.get(Role.MIDDLEWARE, 0.0) + 0.3
    if Role.INTEGRATION in scores and external_imports:
        scores[Role.INTEGRATION] += 0.2
    # A class is not a Validator just because its name ends in "Schema" when it is also a model.
    if Role.MODEL in scores:
        scores.pop(Role.VALIDATOR, None)
    return scores


def graph_boosts(
    scores: dict[Role, float], used_by_roles: set[Role], accesses_model: bool, kind: str
) -> dict[Role, float]:
    out = dict(scores)
    if kind != "class":
        return out
    if used_by_roles & {Role.CONTROLLER, Role.ROUTE} and Role.SERVICE in out:
        out[Role.SERVICE] += 0.2
    if accesses_model:
        out[Role.REPOSITORY] = out.get(Role.REPOSITORY, 0.0) + 0.4
    return out


def decide(scores: dict[Role, float]) -> tuple[list[str], float]:
    kept = {r: min(1.0, s) for r, s in scores.items() if s >= THRESHOLD - 1e-9}
    if not kept:
        return [], round(max(scores.values()), 2) if scores else 0.0
    # One primary role per symbol, plus Model/Validator which can coexist with nothing else meaningful.
    best = max(kept.items(), key=lambda kv: (kv[1], kv[0].value))
    return [best[0].value], round(best[1], 2)
