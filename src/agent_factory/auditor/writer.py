"""Write a GraphFragment to Neo4j: full, or incremental (only what changed)."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..db.repos import CodeRepo
from ..schema.model import Label, Rel
from ..schema.uids import file_uid
from .model import EdgeRow, GraphFragment


@dataclass
class WriteStats:
    files_written: int = 0
    files_deleted: int = 0
    symbols_written: int = 0
    symbols_deleted: int = 0
    edges_written: int = 0
    edges_deleted: int = 0
    by_rel: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _path_of(uid: str) -> str | None:
    """File path embedded in a file/symbol uid, or None for modules etc."""
    if ":file:" in uid:
        return uid.split(":file:", 1)[1]
    if ":sym:" in uid:
        return uid.split(":sym:", 1)[1].split("#", 1)[0]
    return None


def write_fragment(
    repo: CodeRepo,
    frag: GraphFragment,
    run_uid: str,
    changed: set[str] | None = None,
    removed: set[str] | None = None,
) -> WriteStats:
    """changed=None: full write. Otherwise only files in `changed` (plus edges touching them) are written."""
    stats = WriteStats()
    pid = frag.project_id
    removed = removed or set()
    if removed:
        stats.files_deleted = repo.delete_file_subgraph([file_uid(pid, p) for p in sorted(removed)])

    def in_scope(path: str | None) -> bool:
        return changed is None or (path is not None and path in changed)

    repo.upsert_modules(frag.modules, run_uid)
    files = [f for f in frag.files if in_scope(f["path"])]
    stats.files_written = repo.upsert_files(files, run_uid)
    if files:
        repo.store.write_batches(
            "UNWIND $rows AS row MATCH (f:File {uid: row.uid}) SET f.external_imports = row.ext",
            [{"uid": f["uid"], "ext": f.get("external_imports", [])} for f in files],
        )
    symbols = [s for s in frag.symbols if in_scope(s.props["path"])]
    stats.symbols_written = repo.upsert_symbols(
        [
            {
                "uid": s.uid,
                "file_uid": s.file_uid,
                "props": s.props,
                "roles": s.roles,
                "role_confidence": s.role_confidence,
            }
            for s in symbols
        ],
        run_uid,
    )
    written_files = [f["uid"] for f in files]
    stats.symbols_deleted = repo.delete_stale_symbols(written_files, run_uid)

    grouped: dict[tuple[str, str, str], list[EdgeRow]] = defaultdict(list)
    for e in frag.edges:
        if e.rel == Rel.DEPENDS_ON.value or in_scope(_path_of(e.src)) or in_scope(_path_of(e.dst)):
            grouped[(e.rel, e.src_label, e.dst_label)].append(e)
    for (rel, src_label, dst_label), rows in sorted(grouped.items()):
        n = repo.upsert_edges(
            rel,
            [{"src": e.src, "dst": e.dst, "props": e.props} for e in rows],
            run_uid,
            Label(src_label),
            Label(dst_label),
        )
        stats.by_rel[rel] = n
        stats.edges_written += n
    # Edges from re-written sources that this run did not produce any more.
    sources = written_files + [s.uid for s in symbols]
    stats.edges_deleted = repo.delete_stale_edges(sources, run_uid)
    stats.edges_deleted += repo.delete_stale_edges(sorted({m["uid"] for m in frag.modules}), run_uid, [Rel.DEPENDS_ON])
    if changed is None:
        stale = repo.stale_files(run_uid)
        if stale:
            stats.files_deleted += repo.delete_file_subgraph(stale)
    repo.delete_empty_modules()
    return stats
