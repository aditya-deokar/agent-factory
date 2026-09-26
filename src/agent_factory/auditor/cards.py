"""Symbol cards: the compact text that gets embedded. Never contains function bodies."""

from __future__ import annotations

import re

from .model import SymbolDef

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_WORD = re.compile(r"[^A-Za-z0-9]+")
EMBEDDABLE_KINDS = {"class", "function", "route", "interface"}


def split_words(name: str) -> list[str]:
    words: list[str] = []
    for part in _NON_WORD.split(name):
        words += [w.lower() for w in _CAMEL.split(part) if w]
    return words


def name_tokens(name: str) -> str:
    return " ".join(split_words(name))


def build_card(sym: SymbolDef, path: str, roles: list[str], used_by: list[str], doc: str | None) -> str:
    role = f", {roles[0]}" if roles else ""
    lines = [f"{sym.name} ({sym.kind}{role}) in {path}"]
    if sym.kind == "route":
        lines = [f"Route {sym.name} in {path}"]
    elif sym.kind in ("function", "method") and sym.signature:
        lines.append(f"Signature: {sym.signature}")
    if sym.methods:
        lines.append("Methods: " + " · ".join(sym.methods[:20]))
    if sym.extends:
        lines.append("Extends: " + ", ".join(sym.extends))
    if sym.hints.get("table"):
        lines.append(f"Table: {sym.hints['table']}")
    if doc:
        lines.append(f"Doc: {doc}")
    if used_by:
        lines.append("Used by: " + ", ".join(used_by[:12]))
    return "\n".join(lines)


def is_embeddable(sym_props: dict, roles: list[str]) -> bool:
    kind = sym_props.get("kind")
    if kind == "method":
        return False
    if kind in EMBEDDABLE_KINDS and (sym_props.get("exported") or kind == "route"):
        return True
    return bool(roles)
