"""Historical memory from `git log`: commits, touched files, co-change coupling.

Authors are stored only as a salted hash (§30). Commit subjects are redacted.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from ..common.paths import git
from ..common.redact import redact

_CONVENTIONAL = re.compile(r"^(\w+)(?:\([^)]*\))?!?:")
_BRACE_RENAME = re.compile(r"\{([^{}]*) => ([^{}]*)\}")


@dataclass
class CommitInfo:
    sha: str
    subject: str
    type: str | None
    author_hash: str
    date: str
    files: list[tuple[str, int, int]] = field(default_factory=list)  # (path, added, deleted)


def salt_for(root: Path) -> str:
    path = root / ".agent-factory" / "cache" / "salt"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(16)
    path.write_text(value, encoding="utf-8")
    return value


def _renamed_path(raw: str) -> str:
    if " => " not in raw:
        return raw
    if "{" in raw:
        return _BRACE_RENAME.sub(lambda m: m.group(2), raw).replace("//", "/")
    return raw.split(" => ", 1)[1]


def read_history(root: Path, limit: int, known: set[str] | None = None, salt: str = "") -> list[CommitInfo]:
    if limit <= 0:
        return []
    out = git(root, "log", f"-n{limit}", "--no-merges", "--numstat", "--format=%x1e%H%x1f%ae%x1f%aI%x1f%s", check=False)
    commits: list[CommitInfo] = []
    for block in out.split("\x1e"):
        block = block.strip("\n")
        if not block:
            continue
        header, *rest = block.split("\n")
        parts = header.split("\x1f")
        if len(parts) < 4:
            continue
        sha, email, date, subject = parts[0], parts[1], parts[2], "\x1f".join(parts[3:])
        if known and sha in known:
            continue
        m = _CONVENTIONAL.match(subject)
        info = CommitInfo(
            sha=sha,
            subject=redact(subject).text[:300],
            type=m.group(1).lower() if m else None,
            author_hash=hashlib.sha256(f"{salt}:{email.lower()}".encode()).hexdigest()[:12],
            date=date,
        )
        for line in rest:
            cols = line.split("\t")
            if len(cols) != 3:
                continue
            added = int(cols[0]) if cols[0].isdigit() else 0
            deleted = int(cols[1]) if cols[1].isdigit() else 0
            info.files.append((_renamed_path(cols[2]), added, deleted))
        commits.append(info)
    return commits
