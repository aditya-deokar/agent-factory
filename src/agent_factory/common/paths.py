"""Repository paths and git plumbing.

Paths stored in the graph are repo-relative POSIX strings on every OS.
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath


class NotAGitRepo(RuntimeError):
    pass


def to_posix(path: str | Path) -> str:
    """Normalize a repo-relative path: forward slashes, no leading './'. Rejects escapes."""
    text = str(path).replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    pure = PurePosixPath(text)
    if pure.is_absolute() or (len(text) > 1 and text[1] == ":"):
        raise ValueError(f"expected a repo-relative path, got {path!r}")
    if ".." in pure.parts:
        raise ValueError(f"path escapes the repository: {path!r}")
    return str(pure) if text else ""


def rel_posix(path: Path, root: Path) -> str:
    return to_posix(path.resolve().relative_to(root.resolve()).as_posix())


def git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout


def find_git_root(start: Path) -> Path:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise NotAGitRepo("git is not installed or not on PATH") from error
    if out.returncode != 0 or not out.stdout.strip():
        raise NotAGitRepo(f"{start} is not inside a git repository")
    return Path(out.stdout.strip()).resolve()


def head_commit(root: Path) -> str | None:
    out = git(root, "rev-parse", "HEAD", check=False).strip()
    return out or None


def is_tracked(root: Path, rel: str) -> bool:
    return bool(git(root, "ls-files", "--", rel, check=False).strip())


def is_dirty(root: Path) -> bool:
    return bool(git(root, "status", "--porcelain", check=False).strip())


def list_repo_files(root: Path) -> list[str]:
    """Tracked + untracked-but-not-ignored files, POSIX, sorted."""
    out = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return sorted({p.replace("\\", "/") for p in out.split("\0") if p})
