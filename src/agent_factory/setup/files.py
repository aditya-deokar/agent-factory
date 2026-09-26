"""Idempotent, merge-don't-clobber file edits used by `agent-factory init`.

Each function returns an `Action` describing what it did (or would do, with
dry_run=True). Nothing outside Agent Factory's own keys/markers is modified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ActionKind = Literal["created", "updated", "unchanged", "skipped"]

MCP_SERVER_NAME = "agent-factory"
STDIO_SERVER = {"command": "agent-factory", "args": ["mcp", "serve"]}
GRAPHACADEMY_URL = "https://mcp.graphacademy.neo4j.com/mcp"

AGENTS_START = "<!-- agent-factory:start -->"
AGENTS_END = "<!-- agent-factory:end -->"


@dataclass
class Action:
    path: str
    kind: ActionKind
    detail: str = ""


def write_text(path: Path, content: str, rel: str, dry_run: bool) -> Action:
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == content:
            return Action(rel, "unchanged")
        kind: ActionKind = "updated"
    else:
        kind = "created"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
    return Action(rel, kind)


def ensure_dir(path: Path, rel: str, dry_run: bool) -> Action:
    if path.is_dir():
        return Action(rel, "unchanged")
    if not dry_run:
        path.mkdir(parents=True, exist_ok=True)
    return Action(rel, "created")


def ensure_file(path: Path, rel: str, dry_run: bool) -> Action:
    if path.exists():
        return Action(rel, "unchanged")
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    return Action(rel, "created")


def append_lines(path: Path, lines: list[str], header: str, rel: str, dry_run: bool) -> Action:
    """Append lines missing from a line-oriented file (e.g. .gitignore)."""
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    present = {line.strip() for line in existing}
    missing = [line for line in lines if line.strip() not in present]
    if not missing:
        return Action(rel, "unchanged")
    block = ([""] if existing and existing[-1].strip() else []) + [header, *missing]
    if not dry_run:
        text = "\n".join([*existing, *block]) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
    return Action(rel, "updated" if existing else "created", f"added {len(missing)} entries")


def merge_mcp_json(path: Path, rel: str, servers_key: str, servers: dict[str, Any], dry_run: bool) -> Action:
    """Add/refresh our servers in an MCP JSON config, keeping every other server."""
    data: dict[str, Any] = {}
    existed = path.exists()
    if existed:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            return Action(rel, "skipped", "existing file is not valid JSON; left untouched")
        if not isinstance(loaded, dict):
            return Action(rel, "skipped", "existing file is not a JSON object; left untouched")
        data = loaded
    section = data.get(servers_key)
    if section is None:
        section = {}
    elif not isinstance(section, dict):
        return Action(rel, "skipped", f"'{servers_key}' is not an object; left untouched")
    changed = [name for name, cfg in servers.items() if section.get(name) != cfg]
    if not changed and existed:
        return Action(rel, "unchanged")
    section.update(servers)
    data[servers_key] = section
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return Action(rel, "updated" if existed else "created", f"servers: {', '.join(changed) or 'none'}")


def merge_marked_section(path: Path, rel: str, body: str, dry_run: bool, title: str = "") -> Action:
    """Replace the content between agent-factory markers, or append a marked section."""
    section = f"{AGENTS_START}\n{body.strip()}\n{AGENTS_END}"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        start, end = text.find(AGENTS_START), text.find(AGENTS_END)
        if start != -1 and end != -1 and end > start:
            new = text[:start] + section + text[end + len(AGENTS_END) :]
        else:
            new = text.rstrip() + "\n\n" + section + "\n"
    else:
        new = (f"{title}\n\n" if title else "") + section + "\n"
    return write_text(path, new, rel, dry_run)
