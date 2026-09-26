"""Detect project facts for `agent-factory init`: languages, check commands, id."""

from __future__ import annotations

import json
import re
import tomllib
from collections import Counter
from pathlib import Path

from ..common.paths import list_repo_files
from ..config.model import ChecksConfig

_EXT_LANG = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".py": "python",
}
_SKIP_DIRS = ("node_modules/", "dist/", "build/", ".venv/", "vendor/")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return (slug or "project")[:63]


def detect_languages(root: Path, files: list[str] | None = None) -> list[str]:
    files = list_repo_files(root) if files is None else files
    counts: Counter[str] = Counter()
    for f in files:
        if any(f.startswith(d) or f"/{d}" in f for d in _SKIP_DIRS) or f.endswith(".d.ts"):
            continue
        lang = _EXT_LANG.get(Path(f).suffix.lower())
        if lang:
            counts[lang] += 1
    return [lang for lang, _ in counts.most_common()]


def _node_runner(root: Path) -> str:
    if (root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (root / "yarn.lock").exists():
        return "yarn"
    if (root / "bun.lockb").exists() or (root / "bun.lock").exists():
        return "bun"
    return "npm"


def detect_checks(root: Path) -> ChecksConfig:
    checks = ChecksConfig()
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts", {}) or {}
        except (json.JSONDecodeError, OSError):
            scripts = {}
        runner = _node_runner(root)
        run = f"{runner} run" if runner in ("npm", "bun") else runner
        if "test" in scripts:
            checks.test = f"{runner} test"
        if "build" in scripts:
            checks.build = f"{run} build"
        if "lint" in scripts:
            checks.lint = f"{run} lint"
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        prefix = "uv run " if (root / "uv.lock").exists() else ""
        tool = data.get("tool", {})
        if checks.test is None and ("pytest" in tool or (root / "tests").is_dir()):
            checks.test = f"{prefix}pytest -q"
        if checks.lint is None and "ruff" in tool:
            checks.lint = f"{prefix}ruff check ."
    return checks
