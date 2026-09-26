"""Diff evidence collector: captures stat and patch diffs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from ..store import EvidenceStore

if TYPE_CHECKING:
    from ...guardrails.diff import DiffFragment


def collect_diff_evidence(
    root: Path,
    store: EvidenceStore,
    base_sha: str | None = None,
) -> None:
    """Capture diff stat and patch into evidence store."""
    base_arg = [base_sha] if base_sha else ["HEAD~1"]

    # 1. Diff stat
    try:
        res = subprocess.run(
            ["git", "diff", "--stat", *base_arg],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        stat_text = res.stdout.strip() or "No file changes detected."
        store.record_text(
            kind="diff",
            rel_dest="diff/stat.txt",
            content=stat_text,
            summary=f"Git diff stat vs {base_sha or 'base'}",
            command=f"git diff --stat {' '.join(base_arg)}",
            exit_code=res.returncode,
        )
    except OSError:
        pass

    # 2. Diff patch
    try:
        res = subprocess.run(
            ["git", "diff", *base_arg],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        patch_text = res.stdout.strip() or "Empty patch."
        store.record_text(
            kind="diff",
            rel_dest="diff/patch.diff",
            content=patch_text,
            summary=f"Git patch vs {base_sha or 'base'}",
            command=f"git diff {' '.join(base_arg)}",
            exit_code=res.returncode,
        )
    except OSError:
        pass
