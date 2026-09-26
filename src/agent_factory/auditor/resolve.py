"""Resolve parsed files into a GraphFragment: modules, files, symbols (with roles) and edges.

Resolution is best effort and conservative: an edge is only written when its
target resolves to a symbol in this project. Every edge records how it was
resolved (`exact` when a type or import proves it, `heuristic` otherwise).
"""

from __future__ import annotations

import json
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..common.redact import redact
from ..schema.model import Rel, Role
from ..schema.uids import file_uid, module_uid, symbol_uid
from .cards import build_card, name_tokens
from .classify import decide, graph_boosts, local_scores
from .model import EdgeRow, GraphFragment, NodeRow, ParsedFile, SymbolDef

_TS_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts")
_CONTAINERS = {"src", "apps", "packages", "lib", "app", "services", "libs", "modules"}
_VIA_PRIORITY = {"constructor": 0, "field": 1, "new": 2, "extends": 3, "reference": 4, "call": 5, "type": 6}


def module_path_of(path: str) -> str:
    parts = path.split("/")[:-1]
    if not parts:
        return "."
    if parts[0] in _CONTAINERS and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0]


def package_name(spec: str) -> str | None:
    if spec.startswith((".", "/")) or not spec:
        return None
    if spec.startswith("node:"):
        return None
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0].split(".")[0]


@dataclass(frozen=True)
class Resolved:
    path: str
    qualname: str


class ProjectIndex:
    def __init__(self, root: Path, parsed: dict[str, ParsedFile]):
        self.root = root
        self.parsed = parsed
        self.paths = set(parsed)
        self.base_url, self.aliases = _load_tsconfig(root)
        self._top: dict[str, dict[str, SymbolDef]] = {
            p: {s.qualname: s for s in pf.symbols if s.parent is None} for p, pf in parsed.items()
        }
        self._by_qual: dict[tuple[str, str], SymbolDef] = {
            (p, s.qualname): s for p, pf in parsed.items() for s in pf.symbols
        }
        self.external: dict[str, set[str]] = defaultdict(set)

    def symbol(self, path: str, qualname: str) -> SymbolDef | None:
        return self._by_qual.get((path, qualname))

    # -- module resolution ------------------------------------------------------

    def resolve_module(self, from_path: str, spec: str, lang: str) -> str | None:
        if lang == "python":
            return self._resolve_python(from_path, spec)
        candidates: list[str] = []
        if spec.startswith("."):
            candidates.append(posixpath.normpath(posixpath.join(posixpath.dirname(from_path), spec)))
        else:
            for prefix, targets in self.aliases.items():
                if prefix.endswith("*") and spec.startswith(prefix[:-1]):
                    rest = spec[len(prefix) - 1 :]
                    candidates += [
                        posixpath.normpath(posixpath.join(self.base_url, t.replace("*", rest))) for t in targets
                    ]
                elif spec == prefix:
                    candidates += [posixpath.normpath(posixpath.join(self.base_url, t)) for t in targets]
            if self.base_url != "." or not candidates:
                candidates.append(posixpath.normpath(posixpath.join(self.base_url, spec)))
        for base in candidates:
            found = self._ts_candidates(base)
            if found:
                return found
        pkg = package_name(spec)
        if pkg:
            self.external[from_path].add(pkg)
        return None

    def _ts_candidates(self, base: str) -> str | None:
        stem = base[:-3] if base.endswith((".js", ".jsx")) and base not in self.paths else base
        options = [base, *(stem + e for e in _TS_EXTS), *(f"{base}/index{e}" for e in _TS_EXTS)]
        return next((o for o in options if o in self.paths), None)

    def _resolve_python(self, from_path: str, spec: str) -> str | None:
        if spec.startswith("."):
            level = len(spec) - len(spec.lstrip("."))
            base = posixpath.dirname(from_path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            rest = spec.lstrip(".").replace(".", "/")
            bases = [posixpath.join(base, rest) if rest else base]
        else:
            rest = spec.replace(".", "/")
            bases = [rest, f"src/{rest}"]
        for b in bases:
            for option in (f"{b}.py", f"{b}/__init__.py"):
                if option in self.paths:
                    return option
        pkg = package_name(spec)
        if pkg:
            self.external[from_path].add(pkg)
        return None

    # -- name resolution --------------------------------------------------------

    def resolve_name(self, path: str, name: str, depth: int = 0) -> Resolved | None:
        """What `name` means at the top level of `path`: a local symbol or an imported one."""
        if depth > 4 or path not in self.parsed:
            return None
        top = self._top.get(path, {})
        if name in top and top[name].kind != "route":
            return Resolved(path, name)
        pf = self.parsed[path]
        for imp in pf.imports:
            if imp.reexport:
                continue
            for imported, local in imp.names:
                if local == name:
                    target = self.resolve_module(path, imp.source, pf.source.lang)
                    if target is None and pf.source.lang == "python":
                        target = self.resolve_module(path, f"{imp.source}.{imported}", "python")
                        if target is not None:
                            return None  # a submodule, not a symbol
                    return self.resolve_export(target, imported, depth + 1) if target else None
            if imp.default == name:
                target = self.resolve_module(path, imp.source, pf.source.lang)
                return self.resolve_export(target, "default", depth + 1) if target else None
        return None

    def namespace_target(self, path: str, name: str) -> str | None:
        pf = self.parsed.get(path)
        if pf is None:
            return None
        for imp in pf.imports:
            if imp.namespace == name and not imp.reexport:
                return self.resolve_module(path, imp.source, pf.source.lang)
        return None

    def resolve_export(self, path: str, name: str, depth: int = 0) -> Resolved | None:
        if depth > 4 or path not in self.parsed:
            return None
        top = self._top.get(path, {})
        if name in top:
            return Resolved(path, name)
        if name == "default":
            default = next((s for s in top.values() if s.qualname == "default"), None)
            if default:
                return Resolved(path, default.qualname)
        pf = self.parsed[path]
        for imp in pf.imports:
            if not imp.reexport:
                continue
            target = self.resolve_module(path, imp.source, pf.source.lang)
            if target is None:
                continue
            if imp.star:
                found = self.resolve_export(target, name, depth + 1)
                if found:
                    return found
            for imported, alias in imp.names:
                if alias == name:
                    return self.resolve_export(target, imported, depth + 1)
        # `import { X } from './x'; export { X }` (TS) and package __init__ re-exports (Python).
        return self.resolve_name(path, name, depth + 1)


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas outside strings (tsconfig is JSONC)."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_tsconfig(root: Path) -> tuple[str, dict[str, list[str]]]:
    path = root / "tsconfig.json"
    if not path.exists():
        return ".", {}
    try:
        options = json.loads(strip_jsonc(path.read_text(encoding="utf-8", errors="replace"))).get("compilerOptions", {})
    except json.JSONDecodeError:
        return ".", {}
    base = posixpath.normpath(options.get("baseUrl", ".") or ".")
    paths = {k: list(v) for k, v in (options.get("paths") or {}).items() if isinstance(v, list)}
    return base, paths


class Resolver:
    def __init__(self, root: Path, project_id: str, parsed: dict[str, ParsedFile]):
        self.project_id = project_id
        self.idx = ProjectIndex(root, parsed)
        self.parsed = parsed
        self.edges: dict[tuple[str, str, str], EdgeRow] = {}

    def uid(self, path: str, qualname: str) -> str:
        return symbol_uid(self.project_id, path, qualname)

    def _add(
        self, rel: Rel, src: str, dst: str, src_label: str = "Symbol", dst_label: str = "Symbol", **props: Any
    ) -> None:
        if src == dst:
            return
        key = (rel.value, src, dst)
        existing = self.edges.get(key)
        if existing is None:
            self.edges[key] = EdgeRow(rel.value, src, dst, src_label, dst_label, dict(props))
            return
        if rel == Rel.USES and _VIA_PRIORITY.get(props.get("via", ""), 9) < _VIA_PRIORITY.get(
            existing.props.get("via", ""), 9
        ):
            existing.props.update(props)
        if rel == Rel.CALLS:
            existing.props["count"] = existing.props.get("count", 1) + 1
        if rel == Rel.ACCESSES and existing.props.get("op") != props.get("op"):
            existing.props["op"] = "rw"

    def _owner(self, path: str, qualname: str | None) -> SymbolDef | None:
        """The symbol that owns a reference: the class for methods, the symbol itself otherwise."""
        if qualname is None:
            return None
        sym = self.idx.symbol(path, qualname)
        if sym is not None and sym.parent is not None:
            return self.idx.symbol(path, sym.parent)
        return sym

    def _class_of(self, path: str, qualname: str | None) -> SymbolDef | None:
        sym = self.idx.symbol(path, qualname) if qualname else None
        if sym is None:
            return None
        return self.idx.symbol(path, sym.parent) if sym.parent else (sym if sym.kind == "class" else None)

    def _type_to_class(self, path: str, tname: str) -> Resolved | None:
        return self.idx.resolve_name(path, tname)

    def _receiver_type(self, path: str, from_q: str | None, receiver: str) -> Resolved | None:
        sym = self.idx.symbol(path, from_q) if from_q else None
        cls = self._class_of(path, from_q)
        if receiver.startswith(("this.", "self.")) and receiver.count(".") == 1:
            field = receiver.split(".", 1)[1]
            tname = cls.field_types.get(field) if cls else None
            return self._type_to_class(path, tname) if tname else None
        if "." in receiver or receiver.endswith("()"):
            return None
        if sym is not None:
            tname = sym.param_types.get(receiver) or sym.local_types.get(receiver)
            if tname:
                return self._type_to_class(path, tname)
        target = self.idx.resolve_name(path, receiver)
        if target is not None:
            tsym = self.idx.symbol(target.path, target.qualname)
            if tsym is not None and tsym.hints.get("instance_of"):
                return self.idx.resolve_name(target.path, tsym.hints["instance_of"])
            if tsym is not None and tsym.kind == "class":
                return target
        return None

    # -- build -------------------------------------------------------------------

    def build(self) -> GraphFragment:
        pid = self.project_id
        frag = GraphFragment(project_id=pid)
        modules: dict[str, dict[str, Any]] = {}
        for path, pf in sorted(self.parsed.items()):
            mpath = module_path_of(path)
            modules.setdefault(
                mpath,
                {
                    "uid": module_uid(pid, mpath),
                    "path": mpath,
                    "kind": "root" if mpath == "." else "dir",
                    "parent_uid": None,
                },
            )
            frag.files.append(
                {
                    "uid": file_uid(pid, path),
                    "path": path,
                    "lang": pf.source.lang,
                    "sha256": pf.source.sha256,
                    "loc": 0,
                    "module_uid": modules[mpath]["uid"],
                    "is_test": pf.source.is_test,
                }
            )
        frag.modules = list(modules.values())
        self._resolve_edges()
        frag.symbols = self._symbol_rows()
        frag.edges = sorted(self.edges.values(), key=lambda e: (e.rel, e.src, e.dst))
        loc = {p: max((s.line_end for s in pf.symbols), default=0) for p, pf in self.parsed.items()}
        ext = {p: sorted(v) for p, v in self.idx.external.items()}
        for f in frag.files:
            f["loc"] = loc.get(f["path"], 0)
            f["external_imports"] = ext.get(f["path"], [])
        frag.stats["unresolved_refs"] = self.unresolved
        return frag

    def _resolve_edges(self) -> None:
        self.unresolved = 0
        pid = self.project_id
        for path, pf in self.parsed.items():
            lang = pf.source.lang
            # IMPORTS (file level) + external packages
            by_target: dict[str, list[str]] = defaultdict(list)
            for imp in pf.imports:
                target_path = self.idx.resolve_module(path, imp.source, lang)
                if target_path and target_path != path:
                    names = [local for _, local in imp.names] + [n for n in (imp.default, imp.namespace) if n]
                    by_target[target_path].extend(names)
            for target_path, names in by_target.items():
                self._add(
                    Rel.IMPORTS,
                    file_uid(pid, path),
                    file_uid(pid, target_path),
                    "File",
                    "File",
                    names=sorted(set(names)),
                )
            for sym in pf.symbols:
                if sym.parent is not None:
                    self._add(Rel.HAS_MEMBER, self.uid(path, sym.parent), self.uid(path, sym.qualname))
            # Refs -> USES / EXTENDS / IMPLEMENTS
            for ref in pf.refs:
                owner = self._owner(path, ref.from_qualname)
                if owner is None:
                    continue
                target = self.idx.resolve_name(path, ref.target)
                if target is None:
                    self.unresolved += ref.via in ("constructor", "field", "new", "extends")
                    continue
                src, dst = self.uid(path, owner.qualname), self.uid(target.path, target.qualname)
                if ref.via == "extends":
                    self._add(Rel.EXTENDS, src, dst, resolution="exact")
                elif ref.via == "implements":
                    self._add(Rel.IMPLEMENTS, src, dst, resolution="exact")
                else:
                    self._add(Rel.USES, src, dst, via=ref.via, resolution="exact")
            # Calls -> CALLS (+ USES for instantiation / static use)
            for call in pf.calls:
                if call.from_qualname is None:
                    continue
                self._resolve_call(path, call.from_qualname, call.receiver, call.method)
            # Table access -> ACCESSES
            for acc in pf.tables:
                if acc.from_qualname is None:
                    continue
                model = self._resolve_table(path, acc.table)
                if model is None:
                    continue
                self._add(
                    Rel.ACCESSES,
                    self.uid(path, acc.from_qualname),
                    self.uid(model.path, model.qualname),
                    op=acc.op,
                    resolution="exact",
                )
            # Routes -> HANDLES
            for sym in pf.symbols:
                if sym.kind == "route":
                    self._route_handles(path, sym)
            # Tests -> COVERS
            if pf.source.is_test:
                for imp in pf.imports:
                    for _, local in imp.names + ((("default", imp.default),) if imp.default else ()):
                        target = self.idx.resolve_name(path, local)
                        if target is not None and not self.parsed[target.path].source.is_test:
                            self._add(Rel.COVERS, file_uid(pid, path), self.uid(target.path, target.qualname), "File")
        # Module dependencies
        weights: dict[tuple[str, str], int] = defaultdict(int)
        for e in list(self.edges.values()):
            if e.rel == Rel.IMPORTS.value:
                a = module_path_of(e.src.split(":file:", 1)[1])
                b = module_path_of(e.dst.split(":file:", 1)[1])
                if a != b:
                    weights[(a, b)] += 1
        for (a, b), w in weights.items():
            self._add(Rel.DEPENDS_ON, module_uid(pid, a), module_uid(pid, b), "Module", "Module", weight=w)

    def _resolve_call(self, path: str, from_q: str, receiver: str | None, method: str) -> None:
        src = self.uid(path, from_q)
        owner = self._owner(path, from_q)
        if receiver is None:
            target = self.idx.resolve_name(path, method)
            if target is None:
                return
            tsym = self.idx.symbol(target.path, target.qualname)
            if tsym is None:
                return
            if tsym.kind == "class" and owner is not None:
                self._add(
                    Rel.USES,
                    self.uid(path, owner.qualname),
                    self.uid(target.path, target.qualname),
                    via="new",
                    resolution="exact",
                )
            elif tsym.kind == "function":
                self._add(Rel.CALLS, src, self.uid(target.path, target.qualname), count=1, resolution="exact")
            return
        cls = self._receiver_type(path, from_q, receiver)
        if cls is None:
            ns = self.idx.namespace_target(path, receiver) if "." not in receiver else None
            if ns:
                target = self.idx.resolve_export(ns, method)
                if target is not None:
                    self._add(Rel.CALLS, src, self.uid(target.path, target.qualname), count=1, resolution="exact")
            return
        csym = self.idx.symbol(cls.path, cls.qualname)
        if csym is None:
            return
        resolution = "exact"
        if method in csym.methods:
            self._add(Rel.CALLS, src, self.uid(cls.path, f"{cls.qualname}.{method}"), count=1, resolution=resolution)
        if owner is not None and owner.qualname != cls.qualname:
            self._add(
                Rel.USES,
                self.uid(path, owner.qualname),
                self.uid(cls.path, cls.qualname),
                via="call",
                resolution=resolution,
            )

    def _resolve_table(self, path: str, table: str) -> Any:
        if "." in table:
            ns, member = table.split(".", 1)
            target_file = self.idx.namespace_target(path, ns)
            target = self.idx.resolve_export(target_file, member) if target_file else None
        else:
            target = self.idx.resolve_name(path, table)
        if target is None:
            return None
        tsym = self.idx.symbol(target.path, target.qualname)
        return target if tsym is not None and tsym.hints.get("table") else None

    def _route_handles(self, path: str, route: SymbolDef) -> None:
        src = self.uid(path, route.qualname)
        handler = route.hints.get("handler")
        if handler:
            self._add(
                Rel.HANDLES,
                src,
                self.uid(path, handler),
                http_method=route.hints.get("http_method"),
                http_path=route.hints.get("http_path"),
            )
            return
        calls = sorted(
            (e for e in self.edges.values() if e.rel == Rel.CALLS.value and e.src == src), key=lambda e: e.dst
        )
        method_calls = [e for e in calls if "." in e.dst.rsplit("#", 1)[-1]]
        chosen: EdgeRow | None = (method_calls or calls)[0] if (method_calls or calls) else None
        if chosen is not None:
            self._add(
                Rel.HANDLES,
                src,
                chosen.dst,
                http_method=route.hints.get("http_method"),
                http_path=route.hints.get("http_path"),
            )

    # -- symbol rows -------------------------------------------------------------

    def _symbol_rows(self) -> list[NodeRow]:
        pid = self.project_id
        uid_to_sym: dict[str, tuple[str, SymbolDef]] = {}
        for path, pf in self.parsed.items():
            for sym in pf.symbols:
                uid_to_sym[self.uid(path, sym.qualname)] = (path, sym)
        # Graph signals for classification.
        local = {u: local_scores(s, p, set(self.idx.external.get(p, set()))) for u, (p, s) in uid_to_sym.items()}
        first_roles = {u: set(decide(sc)[0]) for u, sc in local.items()}
        used_by: dict[str, set[Role]] = defaultdict(set)
        accesses_model: set[str] = set()
        for e in self.edges.values():
            if e.rel in (Rel.USES.value, Rel.CALLS.value, Rel.HANDLES.value) and e.src in uid_to_sym:
                src_owner = self._owner_uid(e.src, uid_to_sym)
                dst_owner = self._owner_uid(e.dst, uid_to_sym)
                for r in first_roles.get(src_owner, set()):
                    used_by[dst_owner].add(Role(r))
            if e.rel == Rel.ACCESSES.value:
                accesses_model.add(self._owner_uid(e.src, uid_to_sym))
        rows: list[NodeRow] = []
        fan_in: dict[str, set[str]] = defaultdict(set)
        for e in self.edges.values():
            if e.rel in (Rel.USES.value, Rel.CALLS.value):
                src_owner = self._owner_uid(e.src, uid_to_sym)
                dst_owner = self._owner_uid(e.dst, uid_to_sym)
                if src_owner != dst_owner and src_owner in uid_to_sym and uid_to_sym[src_owner][1].kind != "const":
                    fan_in[dst_owner].add(uid_to_sym[src_owner][1].name)
        for uid, (path, sym) in sorted(uid_to_sym.items()):
            scores = graph_boosts(local[uid], used_by.get(uid, set()), uid in accesses_model, sym.kind)
            roles, confidence = decide(scores)
            doc = redact(sym.doc).text if sym.doc else None
            signature = redact(sym.signature).text
            props: dict[str, Any] = {
                "name": sym.name,
                "qualname": sym.qualname,
                "kind": sym.kind,
                "path": path,
                "lang": self.parsed[path].source.lang,
                "line_start": sym.line_start,
                "line_end": sym.line_end,
                "loc": sym.loc,
                "signature": signature,
                "doc": doc,
                "exported": sym.exported,
                "parent": sym.parent,
                "decorators": sym.decorators,
                "name_tokens": name_tokens(sym.name),
                "method_names": " ".join(sym.methods),
                "methods": sym.methods,
            }
            for key in ("http_method", "http_path", "table", "validator", "instance_of"):
                if sym.hints.get(key):
                    props[key] = sym.hints[key]
            if sym.kind == "route":
                props["validated"] = bool(sym.hints.get("validated"))
            card = build_card(sym, path, roles, sorted(fan_in.get(uid, set())), doc)
            props["card"] = card
            props["card_hash"] = _hash(card)
            rows.append(NodeRow(uid, props, roles, confidence, file_uid(pid, path)))
        return rows

    def _owner_uid(self, uid: str, uid_to_sym: dict[str, tuple[str, SymbolDef]]) -> str:
        entry = uid_to_sym.get(uid)
        if entry is None or entry[1].parent is None:
            return uid
        return self.uid(entry[0], entry[1].parent)


def _hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
