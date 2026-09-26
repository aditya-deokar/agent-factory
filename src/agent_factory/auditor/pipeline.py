"""`agent-factory audit`: Observe -> Analyze -> Map -> Extract -> Validate -> Store (spec §12).

The auditor never modifies the repository. It writes the code graph, then derives
patterns, constraints and decisions and hands them to the KnowledgeService, which
applies validation and the lifecycle, then revalidates everything else.
"""

from __future__ import annotations

import json
import os
import re
import time
import tomllib
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..common.embed import Embedder, EmbeddingUnavailable, make_embedder
from ..common.paths import head_commit
from ..config import AgentFactoryConfig
from ..db.migrations import apply_migrations
from ..db.repos import AuditRunRepo, CodeRepo, HistoryRepo, KnowledgeRepo
from ..db.stores import GraphStores
from ..memory.knowledge import KnowledgeService
from ..memory.revalidate import revalidate
from ..schema.model import KnowledgeStatus, Label, Rel
from ..schema.uids import audit_run_uid, chunk_uid, commit_uid, doc_uid, file_uid
from .cards import is_embeddable
from .constraints import infer_constraints
from .docs import DocResult, parse_doc
from .git_history import read_history, salt_for
from .graphview import GraphView
from .model import GraphFragment, ParsedFile
from .parsers import parse_source
from .patterns import detect_patterns
from .proposals import adr_proposal, constraint_proposal, pattern_proposal, prose_proposal, relevance
from .resolve import Resolver
from .walker import walk


@dataclass
class AuditOptions:
    full: bool = False
    dry_run: bool = False
    embed: bool = True
    history: bool = True


@dataclass
class AuditReport:
    project_id: str
    run_uid: str | None
    mode: str
    commit: str | None
    duration_s: float = 0.0
    files: dict[str, Any] = field(default_factory=dict)
    roles: dict[str, int] = field(default_factory=dict)
    edges: dict[str, int] = field(default_factory=dict)
    docs: dict[str, int] = field(default_factory=dict)
    history: dict[str, int] = field(default_factory=dict)
    knowledge: dict[str, Any] = field(default_factory=dict)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    revalidation: dict[str, Any] = field(default_factory=dict)
    embeddings: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fragment: GraphFragment | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {k: v for k, v in self.__dict__.items() if k != "fragment"}
        out["duration_s"] = round(self.duration_s, 2)
        return out


def _manifest_deps(root: Path, manifests: list[str]) -> dict[str, str]:
    deps: dict[str, str] = {}
    for rel in manifests:
        path = root / rel
        try:
            if rel.endswith("package.json"):
                data = json.loads(path.read_text(encoding="utf-8"))
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    for name in data.get(section) or {}:
                        deps.setdefault(name, rel)
            elif rel.endswith("pyproject.toml"):
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                for spec in (data.get("project") or {}).get("dependencies") or []:
                    deps.setdefault(re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].lower(), rel)
            elif rel.endswith("requirements.txt"):
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith(("#", "-")):
                        deps.setdefault(re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0].lower(), rel)
        except (OSError, ValueError, tomllib.TOMLDecodeError):
            continue
    return deps


def extract(root: Path, config: AgentFactoryConfig) -> tuple[GraphFragment, list[DocResult], Any]:
    """Walk + parse + resolve + docs. Pure: no database."""
    walked = walk(root, config.index)
    parsed: dict[str, ParsedFile] = {}
    parse_errors = 0
    for src in walked.files:
        if src.lang == "markdown":
            continue
        text = (root / src.path).read_text(encoding="utf-8", errors="replace")
        pf = parse_source(src, text)
        if pf is not None:
            parsed[src.path] = pf
            parse_errors += pf.errors > 0
    frag = Resolver(root, config.project.id, parsed).build()
    frag.external_deps = _manifest_deps(root, walked.manifests)
    frag.stats.update(
        {
            "scanned": len(walked.files),
            "parsed": len(parsed),
            "skipped": walked.skipped,
            "files_with_parse_errors": parse_errors,
            "manifests": walked.manifests,
        }
    )
    docs = [parse_doc(root, s.path) for s in walked.files if s.lang == "markdown"]
    return frag, docs, walked


def run_audit(
    root: Path,
    config: AgentFactoryConfig,
    stores: GraphStores | None,
    opts: AuditOptions | None = None,
    embedder: Embedder | None = None,
    env: Mapping[str, str] | None = None,
) -> AuditReport:
    opts = opts or AuditOptions()
    env = os.environ if env is None else env
    started = time.perf_counter()
    pid = config.project.id
    commit = head_commit(root)
    frag, docs, _walked = extract(root, config)
    report = AuditReport(pid, None, "dry-run" if opts.dry_run else "full", commit, fragment=frag)
    report.files = {k: frag.stats[k] for k in ("scanned", "parsed", "skipped", "files_with_parse_errors")}
    report.roles = dict(Counter(r for s in frag.symbols for r in s.roles).most_common())
    report.edges = dict(sorted(Counter(e.rel for e in frag.edges).items()))
    report.docs = {
        "docs": len(docs),
        "chunks": sum(len(d.chunks) for d in docs),
        "adrs": sum(1 for d in docs if d.adr),
        "prose_rules": sum(len(d.rules) for d in docs),
    }

    if opts.dry_run or stores is None:
        view = GraphView.from_fragment(frag)
        patterns = detect_patterns(view)
        report.candidates = [
            {"kind": "pattern", "title": p.title, "support": len(p.support), "violations": len(p.violations)}
            for p in patterns
        ]
        report.candidates += [
            {"kind": "constraint", "title": c.title, "support": len(c.support), "violations": len(c.violations)}
            for c in infer_constraints(view, patterns)
        ]
        report.duration_s = time.perf_counter() - started
        return report

    store = stores.domain
    apply_migrations(store, config.embeddings.dimensions)
    code = CodeRepo(store, pid)
    runs = AuditRunRepo(store, pid)
    last = runs.last()
    mode = "full" if (opts.full or last is None) else "incremental"
    now = datetime.now(UTC)
    run_uid = audit_run_uid(pid, now)
    report.run_uid, report.mode = run_uid, mode

    code.upsert_project(config.project.name, config.project.languages, external_deps=sorted(frag.external_deps))
    runs.start(run_uid, commit, mode, now.isoformat())

    current = {f["path"]: f["sha256"] for f in frag.files}
    changed: set[str] | None = None
    removed: set[str] = set()
    if mode == "incremental":
        known = code.file_hashes()
        changed = {p for p, h in current.items() if known.get(p) != h}
        removed = set(known) - set(current)
    from .writer import write_fragment

    written = write_fragment(code, frag, run_uid, changed, removed)
    report.files |= {
        "written": written.files_written,
        "deleted": written.files_deleted,
        "changed": len(changed) if changed is not None else len(current),
    }

    _write_docs(code, pid, docs, frag, run_uid)
    if embedder is None and opts.embed:
        embedder = make_embedder(
            config.embeddings.provider,
            config.embeddings.model,
            config.embeddings.dimensions,
            env,
            root / ".agent-factory" / "cache",
        )
    report.embeddings = _embed(code, frag, docs, pid, embedder if opts.embed else None, changed, report.warnings)

    if opts.history and config.index.git_history_commits > 0:
        report.history = _write_history(root, code, pid, config.index.git_history_commits)

    view = GraphView.load(store, pid)
    report.knowledge, touched = _ingest_knowledge(root, config, store, view, docs, frag)
    service = KnowledgeService(store, pid, root, config.validation, view)
    report.revalidation = revalidate(service, touched).to_dict()
    report.candidates = [
        {
            "uid": k["uid"],
            "kind": k["kind"],
            "title": k["title"],
            "confidence": k.get("confidence"),
            "support": k.get("support_count"),
            "violations": k.get("violation_count"),
        }
        for k in KnowledgeRepo(store, pid).list_knowledge(status=KnowledgeStatus.CANDIDATE)
    ]
    report.duration_s = time.perf_counter() - started
    runs.finish(
        run_uid,
        datetime.now(UTC).isoformat(),
        {
            "files": len(current),
            "symbols": len(frag.symbols),
            "edges": len(frag.edges),
            "changed": report.files["changed"],
            "knowledge": report.knowledge.get("by_status", {}),
            "duration_s": round(report.duration_s, 2),
        },
    )
    return report


def _write_docs(code: CodeRepo, pid: str, docs: list[DocResult], frag: GraphFragment, run_uid: str) -> None:
    code.upsert_docs(
        [{"uid": doc_uid(pid, d.path), "path": d.path, "kind": d.kind, "title": d.title} for d in docs], run_uid
    )
    chunks = []
    for d in docs:
        prev = None
        for c in d.chunks:
            uid = chunk_uid(pid, d.path, c.index)
            chunks.append(
                {
                    "uid": uid,
                    "doc_uid": doc_uid(pid, d.path),
                    "index": c.index,
                    "text": c.text,
                    "heading": c.heading,
                    "line_start": c.line_start,
                    "prev_uid": prev,
                }
            )
            prev = uid
    code.upsert_chunks(chunks, run_uid)
    code.delete_stale_docs(run_uid)
    # DESCRIBES: a chunk that names a class/function/route owner explicitly.
    names: dict[str, str] = {}
    for s in frag.symbols:
        name = s.props["name"]
        if s.props["kind"] in ("class", "function") and (len(name) >= 6 or s.roles) and s.props.get("exported"):
            names.setdefault(name, s.uid)
    if not names:
        return
    pattern = re.compile(r"\b(" + "|".join(sorted(map(re.escape, names), key=len, reverse=True)) + r")\b")
    rows = [{"src": c["uid"], "dst": names[m]} for c in chunks for m in sorted(set(pattern.findall(str(c["text"]))))]
    code.upsert_edges(Rel.DESCRIBES, rows, run_uid, Label.DOC_CHUNK, Label.SYMBOL)


def _embed(
    code: CodeRepo,
    frag: GraphFragment,
    docs: list[DocResult],
    pid: str,
    embedder: Embedder | None,
    changed: set[str] | None,
    warnings: list[str],
) -> dict[str, Any]:
    if embedder is None:
        return {"enabled": False, "reason": "disabled (--no-embed) or no OPENAI_API_KEY"}
    have = code.card_hashes(embedder.model)
    targets = [s for s in frag.symbols if is_embeddable(s.props, s.roles) and have.get(s.uid) != s.props["card_hash"]]
    chunks = [(chunk_uid(pid, d.path, c.index), c.text) for d in docs for c in d.chunks]
    try:
        if targets:
            vectors = embedder.embed([s.props["card"] for s in targets])
            code.set_embeddings(
                Label.SYMBOL, [{"uid": s.uid, "embedding": v, "model": embedder.model} for s, v in zip(targets, vectors, strict=True)]
            )
        if chunks:
            vectors = embedder.embed([t for _, t in chunks])
            code.set_embeddings(
                Label.DOC_CHUNK, [{"uid": u, "embedding": v, "model": embedder.model} for (u, _), v in zip(chunks, vectors, strict=True)]
            )
    except EmbeddingUnavailable as error:
        warnings.append(f"embeddings skipped: {error}")
        return {"enabled": False, "reason": str(error)}
    return {
        "enabled": True,
        "model": embedder.model,
        "symbols": len(targets),
        "chunks": len(chunks),
        "cache_hits": getattr(embedder, "hits", None),
        "cache_misses": getattr(embedder, "misses", None),
    }


def _write_history(root: Path, code: CodeRepo, pid: str, limit: int) -> dict[str, int]:
    history = HistoryRepo(code.store, pid)
    commits = read_history(root, limit, history.known_commits(), salt_for(root))
    history.upsert_commits(
        [
            {
                "uid": commit_uid(pid, c.sha),
                "sha": c.sha,
                "message": c.subject,
                "type": c.type,
                "author_hash": c.author_hash,
                "date": c.date,
                "file_count": len(c.files),
            }
            for c in commits
        ]
    )
    history.upsert_touches(
        [
            {"commit_uid": commit_uid(pid, c.sha), "file_uid": file_uid(pid, path), "added": a, "deleted": d}
            for c in commits
            for path, a, d in c.files
        ]
    )
    pairs = history.recompute_co_changes()
    return {"new_commits": len(commits), "co_change_pairs": pairs}


def _ingest_knowledge(
    root: Path, config: AgentFactoryConfig, store: Any, view: GraphView, docs: list[DocResult], frag: GraphFragment
) -> tuple[dict[str, Any], set[str]]:
    pid = config.project.id
    service = KnowledgeService(store, pid, root, config.validation, view)
    patterns = detect_patterns(view)
    constraints = infer_constraints(view, patterns)
    manifest = next(iter(sorted(set(frag.external_deps.values()))), None)
    results: list[tuple[str, Any, Any]] = []
    for p in patterns:
        results.append(("pattern", p, service.submit(pattern_proposal(view, p))))
    for c in constraints:
        results.append(("constraint", c, service.submit(constraint_proposal(view, c, manifest))))
    adrs = [d.adr for d in docs if d.adr is not None]
    adr_results = {a.adr_id: service.submit(adr_proposal(a)) for a in adrs}
    for d in docs:
        for rule in d.rules:
            results.append(("prose", rule, service.submit(prose_proposal(rule))))
    # Links: FOLLOWS (symbols -> pattern), CONSTRAINED_BY (scope -> constraint)
    for kind, item, res in results:
        if res.uid is None:
            continue
        if kind == "pattern":
            service.link_symbols(res.uid, Rel.FOLLOWS, [u for u in item.support if u in view.symbols])
        elif kind == "constraint":
            service.link_symbols(
                res.uid, Rel.CONSTRAINED_BY, [u for u in item.support + item.violations if u in view.symbols]
            )
    # SUPERSEDES between ADRs, ESTABLISHES from ADR decisions to what they motivate.
    for adr in adrs:
        res = adr_results[adr.adr_id]
        if res.uid is None:
            continue
        for old in adr.supersedes:
            old_uid = adr_results[old].uid if old in adr_results else None
            if old_uid:
                service.repo.link(res.uid, Rel.SUPERSEDES, old_uid)
        for new in adr.superseded_by:
            new_uid = adr_results[new].uid if new in adr_results else None
            if new_uid:
                service.repo.link(new_uid, Rel.SUPERSEDES, res.uid)
        text = f"{adr.title} {adr.decision}"
        for kind, item, kres in results:
            if kres.uid is None or kind == "prose":
                continue
            if relevance(text, f"{item.title} {item.claim}") >= 0.3:
                service.repo.link(res.uid, Rel.ESTABLISHES, kres.uid)
    touched = {r.uid for _, _, r in results if r.uid} | {r.uid for r in adr_results.values() if r.uid}
    all_results = [r for _, _, r in results] + list(adr_results.values())
    summary = {
        "proposed": len(all_results),
        "created": sum(r.outcome == "created" for r in all_results),
        "updated": sum(r.outcome == "updated" for r in all_results),
        "rejected": sum(r.outcome == "rejected" for r in all_results),
        "by_status": dict(Counter(r.status for r in all_results if r.status)),
    }
    return summary, touched
