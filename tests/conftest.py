"""Shared test helpers."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
}


def git(cwd: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **GIT_ENV}
    if date:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = date
    out = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return out.stdout


def make_repo(root: Path, files: dict[str, str], message: str = "init") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "core.autocrlf", "false")
    write_files(root, files)
    git(root, "add", "-A")
    git(root, "commit", "-qm", message)
    return root


def write_files(root: Path, files: dict[str, str]) -> None:
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return make_repo(
        tmp_path / "shop",
        {
            "package.json": '{"name": "shop", "scripts": {"test": "vitest run", "build": "tsc", "lint": "eslint ."}}',
            "src/services/token.service.ts": "export class TokenService {}\n",
            "src/app.ts": "import { TokenService } from './services/token.service';\n",
            "README.md": "# Shop\n",
        },
    )
