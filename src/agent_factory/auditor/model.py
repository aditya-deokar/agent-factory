"""Data the auditor extracts. Pure data: easy to snapshot, reusable for diff analysis (Phase 8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CODE_LANGS = ("typescript", "javascript", "python")


@dataclass(frozen=True)
class SourceFile:
    path: str  # repo-relative POSIX
    lang: str  # typescript | javascript | python | markdown
    sha256: str
    size: int
    is_test: bool = False


@dataclass
class SymbolDef:
    name: str
    qualname: str  # "TokenService", "TokenService.create", "route:POST /teams"
    kind: str  # class | function | method | interface | type | const | route
    line_start: int
    line_end: int
    signature: str = ""
    doc: str | None = None
    exported: bool = False
    parent: str | None = None  # qualname of the owning class, for methods
    decorators: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    implements: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    param_types: dict[str, str] = field(default_factory=dict)  # name -> type name
    field_types: dict[str, str] = field(default_factory=dict)  # this.x -> type name (classes)
    local_types: dict[str, str] = field(default_factory=dict)  # const x = new X()
    ctor_types: list[str] = field(default_factory=list)  # constructor-injected types (classes)
    returns_jsx: bool = False
    loc: int = 0
    hints: dict[str, Any] = field(default_factory=dict)  # table, validator, route, instance_of, framework


@dataclass(frozen=True)
class ImportRef:
    source: str  # module specifier as written
    names: tuple[tuple[str, str], ...] = ()  # (imported, local)
    default: str | None = None
    namespace: str | None = None
    type_only: bool = False
    reexport: bool = False  # export { X } from / export * from
    star: bool = False
    line: int = 0


@dataclass(frozen=True)
class RefUse:
    """`from_qualname` refers to identifier `target` (resolved later)."""

    from_qualname: str | None
    target: str
    via: str  # new | constructor | field | type | reference | extends | implements
    line: int


@dataclass(frozen=True)
class CallRef:
    from_qualname: str | None
    receiver: str | None  # "this.tokens", "controller", "db", None for bare calls
    method: str
    line: int


@dataclass(frozen=True)
class TableAccess:
    from_qualname: str | None
    table: str  # identifier ("teams") or "ns.teams"
    op: str  # read | write
    line: int


@dataclass
class ParsedFile:
    source: SourceFile
    symbols: list[SymbolDef] = field(default_factory=list)
    imports: list[ImportRef] = field(default_factory=list)
    refs: list[RefUse] = field(default_factory=list)
    calls: list[CallRef] = field(default_factory=list)
    tables: list[TableAccess] = field(default_factory=list)
    errors: int = 0  # tree-sitter ERROR nodes

    @property
    def path(self) -> str:
        return self.source.path


@dataclass
class NodeRow:
    """A node to write: label-specific properties + role labels for symbols."""

    uid: str
    props: dict[str, Any]
    roles: list[str] = field(default_factory=list)
    role_confidence: float = 0.0
    file_uid: str | None = None


@dataclass
class EdgeRow:
    rel: str
    src: str
    dst: str
    src_label: str = "Symbol"
    dst_label: str = "Symbol"
    props: dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphFragment:
    """Everything one audit (or one diff) extracted, before it is written."""

    project_id: str
    modules: list[dict[str, Any]] = field(default_factory=list)
    files: list[dict[str, Any]] = field(default_factory=list)
    symbols: list[NodeRow] = field(default_factory=list)
    edges: list[EdgeRow] = field(default_factory=list)
    external_deps: dict[str, str] = field(default_factory=dict)  # package -> manifest file
    stats: dict[str, Any] = field(default_factory=dict)

    def edges_of(self, rel: str) -> list[EdgeRow]:
        return [e for e in self.edges if e.rel == rel]

    def normalized(self) -> dict[str, Any]:
        """Deterministic, timestamp-free view for golden snapshots."""
        return {
            "modules": sorted(m["path"] for m in self.modules),
            "files": sorted((f["path"], f["lang"], f["is_test"]) for f in self.files),
            "symbols": sorted(
                (s.props["path"], s.props["qualname"], s.props["kind"], tuple(sorted(s.roles))) for s in self.symbols
            ),
            "edges": sorted(
                {
                    (e.rel, _short(e.src), _short(e.dst), tuple(sorted((k, str(v)) for k, v in e.props.items())))
                    for e in self.edges
                }
            ),
            "external_deps": sorted(self.external_deps),
        }


def _short(uid: str) -> str:
    """Drop the project prefix so snapshots do not depend on the test's project id."""
    return uid.split(":", 1)[1] if ":" in uid else uid
