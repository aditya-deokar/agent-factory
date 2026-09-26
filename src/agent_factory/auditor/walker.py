"""Choose which files the auditor reads (git-aware, config-filtered, secret-safe)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path, PurePosixPath

from ..common.paths import list_repo_files
from ..common.redact import is_secret_file
from ..config.model import IndexConfig
from .model import SourceFile

_LANG_BY_EXT = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
    ".md": "markdown",
    ".mdx": "markdown",
}
_LOCKFILES = {"package-lock.json", "pnpm-lock.yaml", "yarn.lock", "poetry.lock", "uv.lock", "bun.lockb"}
_TEST_RE = re.compile(
    r"(^|/)(__tests__|tests?|spec)/|\.(test|spec)\.[cm]?[jt]sx?$|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$"
)
MANIFESTS = ("package.json", "pyproject.toml", "requirements.txt")


@dataclass
class WalkResult:
    files: list[SourceFile] = field(default_factory=list)
    manifests: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


@lru_cache(maxsize=256)
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Glob with `**` support, matched against POSIX paths ("**/x" also matches "x")."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


def language_of(path: str) -> str | None:
    return _LANG_BY_EXT.get(PurePosixPath(path).suffix.lower())


def is_test_path(path: str) -> bool:
    return bool(_TEST_RE.search(path))


def is_generated(path: str, head: bytes) -> bool:
    name = PurePosixPath(path).name
    if name in _LOCKFILES or name.endswith((".min.js", ".d.ts", ".map")):
        return True
    return b"@generated" in head[:512] or b"DO NOT EDIT" in head[:512]


def sha256_text(data: bytes) -> str:
    # Normalize line endings so a Windows checkout hashes like a Linux one.
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


def walk(root: Path, index: IndexConfig, files: list[str] | None = None) -> WalkResult:
    result = WalkResult()
    candidates = list_repo_files(root) if files is None else files
    max_bytes = index.max_file_kb * 1024
    for rel in candidates:
        if rel in MANIFESTS or rel.endswith(("/package.json", "/pyproject.toml")):
            if not matches_any(rel, index.exclude):
                result.manifests.append(rel)
            continue
        if is_secret_file(rel):
            result.skip("secret_file")
            continue
        lang = language_of(rel)
        if lang is None:
            result.skip("unsupported_type")
            continue
        if not matches_any(rel, index.include) or matches_any(rel, index.exclude):
            result.skip("excluded")
            continue
        path = root / rel
        try:
            size = path.stat().st_size
        except OSError:
            result.skip("missing")
            continue
        if size > max_bytes:
            result.skip("too_large")
            continue
        data = path.read_bytes()
        if b"\x00" in data[:8192]:
            result.skip("binary")
            continue
        if is_generated(rel, data):
            result.skip("generated")
            continue
        result.files.append(SourceFile(rel, lang, sha256_text(data), size, is_test_path(rel)))
    return result
