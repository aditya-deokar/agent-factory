"""Diff analysis (spec §21, §22): parses git diff into a DiffFragment.

Analyzes changed files with Phase 3 adapters, classifies roles, and builds an
overlay GraphView so guardrails can evaluate constraints and reuse against both
the existing repository and the proposed changes.
"""

from __future__ import annotations

import json
import posixpath
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..auditor.classify import decide, local_scores
from ..auditor.graphview import GraphView, Sym
from ..auditor.model import EdgeRow, NodeRow, ParsedFile, SourceFile
from ..auditor.parsers import parse_source
from ..auditor.resolve import Resolver
from ..common.paths import git
from ..common.redact import redact
from ..schema.model import Rel, Role
from ..schema.uids import file_uid, symbol_uid

_CODE_EXTS = {
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".py": "python",
}


@dataclass
class DiffFile:
    path: str  # POSIX repo-relative
    status: str  # "A" (added), "M" (modified), "D" (deleted)
    added_lines: int = 0
    deleted_lines: int = 0
    parsed: ParsedFile | None = None
    is_test: bool = False


@dataclass
class DiffFragment:
    project_id: str
    base_sha: str | None
    head_sha: str | None
    files: list[DiffFile] = field(default_factory=list)
    added_symbols: list[NodeRow] = field(default_factory=list)
    modified_symbols: list[NodeRow] = field(default_factory=list)
    deleted_symbols: list[str] = field(default_factory=list)
    new_edges: list[EdgeRow] = field(default_factory=list)
    removed_edges: list[EdgeRow] = field(default_factory=list)
    new_external_deps: dict[str, str] = field(default_factory=dict)  # dep -> manifest
    total_added_lines: int = 0
    total_deleted_lines: int = 0
    overlay_view: GraphView | None = None

    def changed_paths(self) -> set[str]:
        return {f.path for f in self.files}

    def files_by_path(self) -> dict[str, DiffFile]:
        return {f.path: f for f in self.files}


def _lang_of(path: str) -> str | None:
    p = path.lower()
    for ext, lang in _CODE_EXTS.items():
        if p.endswith(ext):
            return lang
    return None


def _is_test_file(path: str) -> bool:
    p = path.lower()
    return (
        "/test/" in p
        or "/tests/" in p
        or "/__tests__/" in p
        or p.startswith("test_")
        or p.endswith(("_test.py", ".test.ts", ".spec.ts", ".test.js", ".spec.js"))
    )


def _git_output(root: Path, *args: str) -> str:
    try:
        return git(root, *args, check=False)
    except Exception:
        return ""


def _base_file_content(root: Path, base_sha: str | None, rel_path: str) -> str | None:
    if not base_sha:
        return None
    try:
        res = subprocess.run(
            ["git", "show", f"{base_sha}:{rel_path}"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        return res.stdout if res.returncode == 0 else None
    except OSError:
        return None


def _extract_manifest_deps(content: str, filename: str) -> set[str]:
    deps: set[str] = set()
    if filename == "package.json":
        try:
            data = json.loads(content)
            deps.update((data.get("dependencies") or {}).keys())
            deps.update((data.get("devDependencies") or {}).keys())
        except json.JSONDecodeError:
            pass
    elif filename == "pyproject.toml":
        # Simple extraction of dependencies
        for line in content.splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("["):
                pkg = line.split("=")[0].strip().strip('"').strip("'")
                if pkg and not pkg.startswith("#"):
                    deps.add(pkg)
    elif filename in ("requirements.txt", "requirements-dev.txt"):
        for line in content.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pkg = line.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].strip()
                if pkg:
                    deps.add(pkg)
    return deps


def analyze_diff(
    root: Path,
    base_sha: str | None = None,
    worktree: Path | None = None,
    project_id: str = "project",
    stored_view: GraphView | None = None,
) -> DiffFragment:
    """Analyze changes between base_sha and worktree/root, producing a DiffFragment."""
    repo_dir = worktree or root
    head_sha = _git_output(repo_dir, "rev-parse", "HEAD").strip() or None

    # Determine base_sha if not given
    if not base_sha:
        base_sha = (
            _git_output(repo_dir, "merge-base", "HEAD", "origin/main").strip()
            or _git_output(repo_dir, "merge-base", "HEAD", "main").strip()
            or _git_output(repo_dir, "rev-parse", "HEAD~1").strip()
            or None
        )

    # 1. Collect status of changed files
    file_statuses: dict[str, str] = {}
    if base_sha:
        diff_names = _git_output(repo_dir, "diff", "--name-status", base_sha)
        for line in diff_names.splitlines():
            parts = line.strip().split(maxsplit=1)
            if len(parts) == 2:
                st, p = parts[0][0], parts[1].replace("\\", "/")
                file_statuses[p] = st
    else:
        # No base_sha: check staged and unstaged diff vs empty
        status_out = _git_output(repo_dir, "status", "--porcelain")
        for line in status_out.splitlines():
            if len(line) >= 4:
                st = line[:2].strip()
                p = line[3:].strip().replace("\\", "/")
                file_statuses[p] = "A" if "A" in st or "?" in st else "M"

    # Untracked files
    untracked = _git_output(repo_dir, "ls-files", "--others", "--exclude-standard")
    for line in untracked.splitlines():
        p = line.strip().replace("\\", "/")
        if p:
            file_statuses[p] = "A"

    # 2. Collect numstat
    numstats: dict[str, tuple[int, int]] = {}
    if base_sha:
        diff_num = _git_output(repo_dir, "diff", "--numstat", base_sha)
        for line in diff_num.splitlines():
            parts = line.strip().split("\t")
            if len(parts) >= 3:
                try:
                    add = int(parts[0]) if parts[0] != "-" else 0
                    dele = int(parts[1]) if parts[1] != "-" else 0
                    p = parts[2].replace("\\", "/")
                    numstats[p] = (add, dele)
                except ValueError:
                    pass

    # 3. Parse changed code files and build diff files
    diff_files: list[DiffFile] = []
    parsed_files: dict[str, ParsedFile] = {}
    new_external_deps: dict[str, str] = {}

    total_added = 0
    total_deleted = 0

    for rel_path, status in sorted(file_statuses.items()):
        full_path = repo_dir / rel_path
        added_lines, deleted_lines = numstats.get(rel_path, (0, 0))

        # Check manifest additions
        manifest_names = ("package.json", "pyproject.toml", "requirements.txt")
        file_name = posixpath.basename(rel_path)
        if file_name in manifest_names and full_path.exists():
            curr_text = full_path.read_text(encoding="utf-8", errors="replace")
            base_text = _base_file_content(repo_dir, base_sha, rel_path) or ""
            curr_deps = _extract_manifest_deps(curr_text, file_name)
            base_deps = _extract_manifest_deps(base_text, file_name)
            for dep in curr_deps - base_deps:
                new_external_deps[dep] = rel_path

        parsed: ParsedFile | None = None
        lang = _lang_of(rel_path)
        if lang and status != "D" and full_path.exists():
            text = full_path.read_text(encoding="utf-8", errors="replace")
            source = SourceFile(
                path=rel_path,
                lang=lang,
                sha256="",
                size=len(text),
                is_test=_is_test_file(rel_path),
            )
            parsed = parse_source(source, text)
            if parsed is not None:
                parsed_files[rel_path] = parsed
            if added_lines == 0 and status == "A":
                added_lines = len(text.splitlines())

        total_added += added_lines
        total_deleted += deleted_lines

        diff_files.append(
            DiffFile(
                path=rel_path,
                status=status,
                added_lines=added_lines,
                deleted_lines=deleted_lines,
                parsed=parsed,
                is_test=_is_test_file(rel_path),
            )
        )

    # 4. Resolve symbols and edges in the diff
    frag = Resolver(repo_dir, project_id, parsed_files).build() if parsed_files else None

    added_symbols: list[NodeRow] = []
    modified_symbols: list[NodeRow] = []
    deleted_symbols: list[str] = []

    existing_uids = set(stored_view.symbols) if stored_view else set()

    if frag:
        for s in frag.symbols:
            if s.uid in existing_uids:
                modified_symbols.append(s)
            else:
                added_symbols.append(s)

    new_edges = frag.edges if frag else []

    # 5. Build overlay GraphView
    overlay = GraphView(project_id)
    if stored_view:
        # copy base symbols
        for uid, sym in stored_view.symbols.items():
            overlay.symbols[uid] = sym
        for path, info in stored_view.files.items():
            overlay.files[path] = info
        for rel, src_map in stored_view.out.items():
            for src, dsts in src_map.items():
                for dst in dsts:
                    overlay.add_edge(rel, src, dst, stored_view.edge_props.get((rel, src, dst)))
        overlay.external_deps.update(stored_view.external_deps)

    # overlay new symbols
    if frag:
        for row in frag.symbols:
            p = row.props
            overlay.symbols[row.uid] = Sym(
                uid=row.uid,
                name=p["name"],
                kind=p["kind"],
                path=p["path"],
                roles=set(row.roles),
                parent_uid=f"{row.uid.split('#', 1)[0]}#{p['parent']}" if p.get("parent") else None,
                methods=list(p.get("methods") or []),
                line_start=p.get("line_start", 1),
                line_end=p.get("line_end", 1),
                exported=bool(p.get("exported")),
                props=dict(p),
            )
        for f in frag.files:
            overlay.files[f["path"]] = {
                "uid": f["uid"],
                "is_test": f["is_test"],
                "external_imports": list(f.get("external_imports") or []),
            }
        for e in frag.edges:
            overlay.add_edge(e.rel, e.src, e.dst, e.props)

    for dep, manifest in new_external_deps.items():
        overlay.external_deps[dep] = manifest

    return DiffFragment(
        project_id=project_id,
        base_sha=base_sha,
        head_sha=head_sha,
        files=diff_files,
        added_symbols=added_symbols,
        modified_symbols=modified_symbols,
        deleted_symbols=deleted_symbols,
        new_edges=new_edges,
        removed_edges=[],
        new_external_deps=new_external_deps,
        total_added_lines=total_added,
        total_deleted_lines=total_deleted,
        overlay_view=overlay,
    )
