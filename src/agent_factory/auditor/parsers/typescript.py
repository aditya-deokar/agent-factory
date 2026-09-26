"""TypeScript / JavaScript adapter (tree-sitter).

Extracts what matters architecturally: classes, methods, exported functions and
constants, imports, constructor-injected types, `new X()`, method calls with
their receivers, Express-style routes, Drizzle tables and data access, Zod
schemas, and JSX usage. Function bodies are scanned, never stored.
"""

from __future__ import annotations

import re
from typing import Any

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from ..model import CallRef, ImportRef, ParsedFile, RefUse, SourceFile, SymbolDef, TableAccess

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "all", "head", "options"}
TABLE_FACTORIES = {"pgTable", "mysqlTable", "sqliteTable"}
VALIDATOR_ROOTS = {"z": "zod", "yup": "yup", "Joi": "joi", "joi": "joi", "v": "valibot"}
READ_METHODS = {"from", "select", "findMany", "findFirst", "findUnique", "query"}
WRITE_METHODS = {"insert", "into", "update", "delete", "upsert"}
_WS = re.compile(r"\s+")
_FUNC_TYPES = {"arrow_function", "function_expression", "function", "generator_function"}
_JSX = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}


def _t(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end(node: Node) -> int:
    return node.end_point[0] + 1


def _compact(text: str, limit: int = 160) -> str:
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _doc_for(node: Node) -> str | None:
    prev = node.prev_named_sibling
    if prev is None and node.parent is not None and node.parent.type == "export_statement":
        prev = node.parent.prev_named_sibling
    if prev is not None and prev.type == "export_statement" and node.parent is not prev:
        prev = None
    if prev is None or prev.type != "comment":
        return None
    raw = _t(prev)
    if not raw.startswith("/**"):
        return None
    lines = [ln.strip().lstrip("*").strip() for ln in raw[3:-2].splitlines()]
    doc = " ".join(ln for ln in lines if ln and not ln.startswith("@"))
    return _compact(doc, 300) or None


def _type_name(annotation: Node | None) -> str | None:
    """`: TokenService` / `: Promise<X>` / `: Foo[]` -> the leading named type ("TokenService", "Promise", "Foo")."""
    if annotation is None:
        return None
    for child in annotation.named_children:
        if child.type == "type_identifier":
            return _t(child)
        if child.type == "generic_type":
            return _t(child.child_by_field_name("name"))
        if child.type == "nested_type_identifier":
            return _t(child).split(".")[-1]
        if child.type == "array_type":
            return _type_name(child)
    return None


def _callee_name(node: Node | None) -> str:
    if node is None:
        return ""
    if node.type == "identifier":
        return _t(node)
    if node.type == "member_expression":
        return _t(node.child_by_field_name("property"))
    return ""


def _member_root(node: Node) -> Node:
    cur = node
    while True:
        if cur.type == "member_expression":
            cur = cur.child_by_field_name("object") or cur
        elif cur.type == "call_expression":
            cur = cur.child_by_field_name("function") or cur
        elif cur.type in ("non_null_expression", "parenthesized_expression", "await_expression") and cur.named_children:
            cur = cur.named_children[0]
        else:
            return cur


def _simple_receiver(node: Node) -> str | None:
    """`this.tokens` / `controller` / `schema.users` -> text; complex chains -> None."""
    if node.type in ("identifier", "this"):
        return _t(node)
    if node.type == "member_expression":
        obj = node.child_by_field_name("object")
        if obj is not None and _simple_receiver(obj) is not None:
            return f"{_simple_receiver(obj)}.{_t(node.child_by_field_name('property'))}"
    return None


class TypeScriptAdapter:
    langs = frozenset({"typescript", "javascript"})

    def parse(self, source: SourceFile, text: str) -> ParsedFile:
        suffix = source.path.rsplit(".", 1)[-1].lower()
        grammar = "tsx" if suffix in ("tsx", "jsx") else ("typescript" if source.lang == "typescript" else "javascript")
        tree = get_parser(grammar).parse(text.encode("utf-8"))
        v = _Visitor(source)
        v.visit_program(tree.root_node)
        return v.result


class _Visitor:
    def __init__(self, source: SourceFile):
        self.result = ParsedFile(source=source)
        self._exported_names: set[str] = set()

    # -- program level -----------------------------------------------------------

    def visit_program(self, root: Node) -> None:
        self.result.errors = _count_errors(root)
        for node in root.named_children:
            self._top(node, exported=False, decorators=[])
        for sym in self.result.symbols:
            if sym.parent is None and sym.name in self._exported_names:
                sym.exported = True

    def _top(self, node: Node, exported: bool, decorators: list[str]) -> None:
        kind = node.type
        if kind == "import_statement":
            self._import(node)
        elif kind == "export_statement":
            self._export(node)
        elif kind in ("class_declaration", "abstract_class_declaration", "class"):
            self._class(node, exported, decorators)
        elif kind in ("function_declaration", "generator_function_declaration"):
            self._function(node, exported)
        elif kind in ("lexical_declaration", "variable_declaration"):
            self._variables(node, exported)
        elif kind in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
            if exported:
                name = _t(node.child_by_field_name("name"))
                sym_kind = "interface" if kind == "interface_declaration" else "type"
                self.result.symbols.append(
                    SymbolDef(
                        name,
                        name,
                        sym_kind,
                        _line(node),
                        _end(node),
                        _compact(_t(node), 120),
                        _doc_for(node),
                        True,
                        loc=_end(node) - _line(node) + 1,
                    )
                )
        elif kind == "expression_statement":
            self._scan(node, None, {})

    def _import(self, node: Node) -> None:
        source = node.child_by_field_name("source")
        spec = _t(source.named_children[0]) if source is not None and source.named_children else _t(source).strip("'\"")
        type_only = any(not c.is_named and _t(c) == "type" for c in node.children)
        names: list[tuple[str, str]] = []
        default = namespace = None
        clause = next((c for c in node.named_children if c.type == "import_clause"), None)
        if clause is not None:
            for c in clause.named_children:
                if c.type == "identifier":
                    default = _t(c)
                elif c.type == "namespace_import":
                    namespace = _t(c.named_children[0]) if c.named_children else None
                elif c.type == "named_imports":
                    for spec_node in c.named_children:
                        if spec_node.type != "import_specifier":
                            continue
                        name = _t(spec_node.child_by_field_name("name"))
                        alias = spec_node.child_by_field_name("alias")
                        names.append((name, _t(alias) if alias is not None else name))
        self.result.imports.append(ImportRef(spec, tuple(names), default, namespace, type_only, line=_line(node)))

    def _export(self, node: Node) -> None:
        source = node.child_by_field_name("source")
        decorators = [self._decorator_name(d) for d in node.children_by_field_name("decorator")]
        if source is not None:  # re-export: export { X } from './x' / export * from './x'
            spec = _t(source.named_children[0]) if source.named_children else _t(source).strip("'\"")
            clause = next((c for c in node.named_children if c.type == "export_clause"), None)
            names: list[tuple[str, str]] = []
            if clause is not None:
                for s in clause.named_children:
                    name = _t(s.child_by_field_name("name"))
                    alias = s.child_by_field_name("alias")
                    names.append((name, _t(alias) if alias is not None else name))
            self.result.imports.append(
                ImportRef(spec, tuple(names), reexport=True, star=clause is None, line=_line(node))
            )
            return
        declaration = node.child_by_field_name("declaration")
        if declaration is not None:
            self._top(declaration, exported=True, decorators=decorators)
            return
        is_default = any(not c.is_named and _t(c) == "default" for c in node.children)
        clause = next((c for c in node.named_children if c.type == "export_clause"), None)
        if clause is not None:
            for s in clause.named_children:
                self._exported_names.add(_t(s.child_by_field_name("name")))
            return
        value = node.child_by_field_name("value") or (node.named_children[-1] if node.named_children else None)
        if value is None:
            return
        if value.type in ("class_declaration", "class", "function_declaration", "function_expression", "function"):
            before = len(self.result.symbols)
            self._top(value, exported=True, decorators=decorators) if value.type != "function_expression" else None
            if value.type in ("class", "function_expression", "function") and len(self.result.symbols) == before:
                self._function(value, True, name_override="default")
        elif value.type == "identifier" and is_default:
            self._exported_names.add(_t(value))
        elif value.type in _FUNC_TYPES:
            self._function(value, True, name_override="default")

    # -- declarations ---------------------------------------------------------

    def _decorator_name(self, node: Node) -> str:
        inner = node.named_children[0] if node.named_children else None
        if inner is None:
            return ""
        if inner.type == "call_expression":
            return _callee_name(inner.child_by_field_name("function")) or _t(inner.child_by_field_name("function"))
        return _t(inner).split(".")[-1]

    def _class(self, node: Node, exported: bool, decorators: list[str]) -> None:
        name_node = node.child_by_field_name("name")
        name = _t(name_node) or "default"
        decorators = decorators + [self._decorator_name(d) for d in node.children_by_field_name("decorator")]
        sym = SymbolDef(
            name,
            name,
            "class",
            _line(node),
            _end(node),
            doc=_doc_for(node),
            exported=exported,
            decorators=[d for d in decorators if d],
            loc=_end(node) - _line(node) + 1,
        )
        heritage = next((c for c in node.named_children if c.type == "class_heritage"), None)
        if heritage is not None:
            for clause in heritage.named_children:
                if clause.type == "extends_clause":
                    value = clause.child_by_field_name("value")
                    if value is not None:
                        sym.extends.append(_t(value).split(".")[-1])
                elif clause.type == "implements_clause":
                    sym.implements += [_t(t).split("<")[0] for t in clause.named_children]
        sym.signature = _compact(
            f"class {name}"
            + (f" extends {', '.join(sym.extends)}" if sym.extends else "")
            + (f" implements {', '.join(sym.implements)}" if sym.implements else "")
        )
        self.result.symbols.append(sym)
        for base in sym.extends:
            self.result.refs.append(RefUse(name, base, "extends", _line(node)))
        for iface in sym.implements:
            self.result.refs.append(RefUse(name, iface, "implements", _line(node)))
        body = node.child_by_field_name("body")
        if body is None:
            return
        for member in body.named_children:
            if member.type == "method_definition":
                self._method(sym, member)
            elif member.type in ("public_field_definition", "field_definition"):
                self._field(sym, member)
            elif member.type in ("method_signature", "abstract_method_signature"):
                sym.methods.append(_t(member.child_by_field_name("name")))

    def _field(self, cls: SymbolDef, node: Node) -> None:
        fname = _t(node.child_by_field_name("name") or node.child_by_field_name("property"))
        tname = _type_name(node.child_by_field_name("type"))
        value = node.child_by_field_name("value")
        if value is not None and value.type == "new_expression":
            tname = tname or _callee_name(value.child_by_field_name("constructor"))
        if fname and tname:
            cls.field_types[fname] = tname
            self.result.refs.append(RefUse(cls.qualname, tname, "field", _line(node)))
        if value is not None:
            self._scan(value, cls.qualname, dict(cls.field_types))

    def _params(self, params: Node | None) -> tuple[dict[str, str], list[tuple[str, str, bool]]]:
        """-> (name->type, [(name, type, is_property)])"""
        types: dict[str, str] = {}
        detail: list[tuple[str, str, bool]] = []
        if params is None:
            return types, detail
        for p in params.named_children:
            if p.type not in ("required_parameter", "optional_parameter"):
                continue
            pattern = p.child_by_field_name("pattern")
            if pattern is None or pattern.type != "identifier":
                continue
            tname = _type_name(p.child_by_field_name("type"))
            is_prop = any(
                c.type in ("accessibility_modifier", "override_modifier") or _t(c) == "readonly" for c in p.children
            )
            if tname:
                types[_t(pattern)] = tname
            detail.append((_t(pattern), tname or "", is_prop))
        return types, detail

    def _method(self, cls: SymbolDef, node: Node) -> None:
        name = _t(node.child_by_field_name("name"))
        params = node.child_by_field_name("parameters")
        types, detail = self._params(params)
        body = node.child_by_field_name("body")
        if name == "constructor":
            for pname, tname, is_prop in detail:
                if tname:
                    cls.ctor_types.append(tname)
                    self.result.refs.append(RefUse(cls.qualname, tname, "constructor", _line(node)))
                    if is_prop:
                        cls.field_types[pname] = tname
            if body is not None:
                self._collect_this_assignments(cls, body)
                self._scan(body, cls.qualname, {**types, **cls.field_types})
            return
        qual = f"{cls.qualname}.{name}"
        ret = node.child_by_field_name("return_type")
        sym = SymbolDef(
            name,
            qual,
            "method",
            _line(node),
            _end(node),
            _compact(f"{name}{_t(params)}{_t(ret)}"),
            _doc_for(node),
            cls.exported,
            parent=cls.qualname,
            param_types=types,
            loc=_end(node) - _line(node) + 1,
        )
        cls.methods.append(name)
        self.result.symbols.append(sym)
        for tname in types.values():
            self.result.refs.append(RefUse(qual, tname, "type", _line(node)))
        if body is not None:
            scope = {**cls.field_types, **types}
            self._scan(body, qual, scope, sym)

    def _collect_this_assignments(self, cls: SymbolDef, body: Node) -> None:
        for node in _walk(body):
            if node.type != "assignment_expression":
                continue
            left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
            if (
                left is not None
                and right is not None
                and left.type == "member_expression"
                and _t(left.child_by_field_name("object")) == "this"
                and right.type == "new_expression"
            ):
                ctor = _callee_name(right.child_by_field_name("constructor"))
                if ctor:
                    cls.field_types[_t(left.child_by_field_name("property"))] = ctor

    def _function(self, node: Node, exported: bool, name_override: str | None = None) -> SymbolDef:
        name = name_override or _t(node.child_by_field_name("name")) or "default"
        params = node.child_by_field_name("parameters")
        types, _ = self._params(params)
        ret = node.child_by_field_name("return_type")
        sym = SymbolDef(
            name,
            name,
            "function",
            _line(node),
            _end(node),
            _compact(f"{name}{_t(params)}{_t(ret)}"),
            _doc_for(node),
            exported,
            param_types=types,
            loc=_end(node) - _line(node) + 1,
        )
        self.result.symbols.append(sym)
        for tname in types.values():
            self.result.refs.append(RefUse(name, tname, "type", _line(node)))
        body = node.child_by_field_name("body")
        if body is not None:
            sym.returns_jsx = any(n.type in _JSX for n in _walk(body))
            self._scan(body, name, dict(types), sym)
        return sym

    def _variables(self, node: Node, exported: bool) -> None:
        for decl in node.named_children:
            if decl.type != "variable_declarator":
                continue
            name_node = decl.child_by_field_name("name")
            if name_node is None or name_node.type != "identifier":
                continue
            name = _t(name_node)
            value = decl.child_by_field_name("value")
            if value is None:
                continue
            if value.type in _FUNC_TYPES:
                sym = self._function(value, exported, name_override=name)
                sym.line_start, sym.doc = _line(node), sym.doc or _doc_for(node)
                continue
            hints: dict[str, Any] = {}
            if value.type == "call_expression":
                fn = value.child_by_field_name("function")
                callee = _callee_name(fn)
                args = value.child_by_field_name("arguments")
                root = _member_root(value)
                if callee in TABLE_FACTORIES and args is not None and args.named_children:
                    first = args.named_children[0]
                    if first.type == "string":
                        hints["table"] = _t(first).strip("'\"`")
                elif root.type == "identifier" and _t(root) in VALIDATOR_ROOTS:
                    hints["validator"] = VALIDATOR_ROOTS[_t(root)]
                elif callee == "Router" or _t(fn) in ("express.Router", "Router"):
                    hints["router"] = True
            elif value.type == "new_expression":
                hints["instance_of"] = _callee_name(value.child_by_field_name("constructor"))
            if not (exported or hints):
                self._scan(value, None, {})
                continue
            sym = SymbolDef(
                name,
                name,
                "const",
                _line(node),
                _end(node),
                _compact(_t(decl), 120),
                _doc_for(node),
                exported,
                hints=hints,
                loc=_end(node) - _line(node) + 1,
            )
            self.result.symbols.append(sym)
            self._scan(value, name, {}, sym)

    # -- body scanning ----------------------------------------------------------

    def _scan(self, root: Node, owner: str | None, scope: dict[str, str], sym: SymbolDef | None = None) -> None:
        stack: list[tuple[Node, str | None, dict[str, str]]] = [(root, owner, scope)]
        while stack:
            node, own, sc = stack.pop()
            t = node.type
            if t == "variable_declarator":
                value = node.child_by_field_name("value")
                name_node = node.child_by_field_name("name")
                if value is not None and value.type == "new_expression" and name_node is not None:
                    ctor = _callee_name(value.child_by_field_name("constructor"))
                    if ctor and name_node.type == "identifier":
                        sc = {**sc, _t(name_node): ctor}
                        if sym is not None:
                            sym.local_types[_t(name_node)] = ctor
                tname = _type_name(node.child_by_field_name("type"))
                if tname and name_node is not None and name_node.type == "identifier":
                    sc = {**sc, _t(name_node): tname}
            elif t == "new_expression":
                ctor = _callee_name(node.child_by_field_name("constructor"))
                if ctor:
                    self.result.refs.append(RefUse(own, ctor, "new", _line(node)))
            elif t == "call_expression":
                route = self._route(node, own, sc)
                if route is not None:
                    own = route
                self._call(node, own)
            elif t in ("jsx_opening_element", "jsx_self_closing_element"):
                tag = _t(node.child_by_field_name("name"))
                if tag[:1].isupper():
                    self.result.refs.append(RefUse(own, tag.split(".")[0], "reference", _line(node)))
            elif t == "binary_expression" and any(_t(c) == "instanceof" for c in node.children):
                right = node.child_by_field_name("right")
                if right is not None and right.type == "identifier":
                    self.result.refs.append(RefUse(own, _t(right), "reference", _line(node)))
            elif t in _FUNC_TYPES and node is not root:
                types, _ = self._params(node.child_by_field_name("parameters"))
                if types:
                    sc = {**sc, **types}
            for child in reversed(node.named_children):
                stack.append((child, own, sc))

    def _call(self, node: Node, owner: str | None) -> None:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None:
            return
        if fn.type == "identifier":
            self.result.calls.append(CallRef(owner, None, _t(fn), _line(node)))
        elif fn.type == "member_expression":
            obj = fn.child_by_field_name("object")
            method = _t(fn.child_by_field_name("property"))
            receiver = _simple_receiver(obj) if obj is not None else None
            if receiver is None and obj is not None:
                root = _member_root(obj)
                receiver = f"{_t(root)}()" if root.type in ("identifier", "this") else None
            self.result.calls.append(CallRef(owner, receiver, method, _line(node)))
            if args is not None and args.named_children and (method in READ_METHODS or method in WRITE_METHODS):
                first = args.named_children[0]
                table = _simple_receiver(first)
                if table and first.type in ("identifier", "member_expression"):
                    op = "write" if method in WRITE_METHODS else "read"
                    self.result.tables.append(TableAccess(owner, table, op, _line(node)))
        if args is not None:
            for arg in args.named_children:
                if arg.type == "identifier":
                    self.result.refs.append(RefUse(owner, _t(arg), "reference", _line(arg)))

    def _route(self, node: Node, owner: str | None, scope: dict[str, str]) -> str | None:
        fn = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if fn is None or fn.type != "member_expression" or args is None or not args.named_children:
            return None
        verb = _t(fn.child_by_field_name("property")).lower()
        first = args.named_children[0]
        if verb not in HTTP_VERBS or first.type not in ("string", "template_string"):
            return None
        path = _t(first).strip("'\"`")
        if not path.startswith("/"):
            return None
        qual = f"route:{verb.upper()} {path}"
        if any(s.qualname == qual for s in self.result.symbols):
            qual = f"{qual} @{_line(node)}"
        validated = any(
            a.type == "call_expression" and _callee_name(a.child_by_field_name("function")) in ("validate", "validator")
            for a in args.named_children[1:]
        )
        self.result.symbols.append(
            SymbolDef(
                f"{verb.upper()} {path}",
                qual,
                "route",
                _line(node),
                _end(node),
                _compact(_t(node), 160),
                None,
                False,
                param_types=dict(scope),
                hints={
                    "http_method": verb.upper(),
                    "http_path": path,
                    "validated": validated,
                    "router": _t(fn.child_by_field_name("object")),
                    "owner": owner,
                },
                loc=_end(node) - _line(node) + 1,
            )
        )
        return qual


def _walk(node: Node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(cur.named_children)


def _count_errors(root: Node) -> int:
    return sum(1 for n in _walk(root) if n.type == "ERROR") if root.has_error else 0
