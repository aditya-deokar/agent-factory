"""Python adapter (tree-sitter).

Classes, methods, top-level functions and constants, imports, `__init__`
dependency injection (typed params and `self.x = X()`), calls with receivers,
FastAPI / Flask route decorators, SQLAlchemy / Django models, Pydantic models.
"""

from __future__ import annotations

import re
from typing import Any

from tree_sitter import Node
from tree_sitter_language_pack import get_parser

from ..model import CallRef, ImportRef, ParsedFile, RefUse, SourceFile, SymbolDef, TableAccess

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "route", "api_route"}
_WS = re.compile(r"\s+")


def _t(node: Node | None) -> str:
    return node.text.decode("utf-8", "replace") if node is not None and node.text is not None else ""


def _line(node: Node) -> int:
    return node.start_point[0] + 1


def _end(node: Node) -> int:
    return node.end_point[0] + 1


def _compact(text: str, limit: int = 160) -> str:
    text = _WS.sub(" ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _type_name(node: Node | None) -> str | None:
    if node is None:
        return None
    text = _t(node).strip().strip("'\"")
    # Optional[X] / X | None / list[X] -> X ; module.Type -> Type
    inner = re.findall(r"[A-Za-z_][A-Za-z0-9_\.]*", text)
    for tok in inner:
        base = tok.split(".")[-1]
        if base not in ("Optional", "None", "list", "List", "dict", "Dict", "Union", "Annotated", "Sequence", "type"):
            return base
    return None


def _docstring(block: Node | None) -> str | None:
    if block is None or not block.named_children:
        return None
    first = _unwrap(block.named_children[0])
    if first.type == "string":
        raw = _t(first).strip("rbuRBU").strip("\"'")
        return _compact(raw, 300) or None
    return None


def _unwrap(node: Node) -> Node:
    """Grammar versions differ: statements may or may not be wrapped in expression_statement."""
    if node.type == "expression_statement" and node.named_children:
        return node.named_children[0]
    return node


def _receiver(node: Node) -> str | None:
    if node.type == "identifier":
        return _t(node)
    if node.type == "attribute":
        obj = node.child_by_field_name("object")
        if obj is not None and _receiver(obj) is not None:
            return f"{_receiver(obj)}.{_t(node.child_by_field_name('attribute'))}"
    return None


def _walk(node: Node):
    stack = [node]
    while stack:
        cur = stack.pop()
        yield cur
        stack.extend(cur.named_children)


class PythonAdapter:
    langs = frozenset({"python"})

    def parse(self, source: SourceFile, text: str) -> ParsedFile:
        tree = get_parser("python").parse(text.encode("utf-8"))
        result = ParsedFile(source=source)
        result.errors = sum(1 for n in _walk(tree.root_node) if n.type == "ERROR") if tree.root_node.has_error else 0
        v = _Visitor(result)
        for node in tree.root_node.named_children:
            v.top(node)
        return result


class _Visitor:
    def __init__(self, result: ParsedFile):
        self.r = result

    def top(self, node: Node, decorators: list[Node] | None = None) -> None:
        t = node.type
        if t == "import_statement":
            for c in node.named_children:
                name = _t(c.child_by_field_name("name") if c.type == "aliased_import" else c)
                alias = _t(c.child_by_field_name("alias")) if c.type == "aliased_import" else name.split(".")[0]
                self.r.imports.append(ImportRef(name, (), None, alias, line=_line(node)))
        elif t == "import_from_statement":
            self._from_import(node)
        elif t == "decorated_definition":
            defn = node.child_by_field_name("definition")
            decos = [d for d in node.named_children if d.type == "decorator"]
            if defn is not None:
                self.top(defn, decos)
        elif t == "class_definition":
            self._class(node, decorators or [])
        elif t == "function_definition":
            self._function(node, None, decorators or [])
        elif t in ("expression_statement", "assignment", "call"):
            inner = _unwrap(node)
            if inner.type == "assignment":
                self._module_assignment(inner)
            else:
                self._scan(node, None, {})

    def _from_import(self, node: Node) -> None:
        module = node.child_by_field_name("module_name")
        spec = _t(module)
        names: list[tuple[str, str]] = []
        star = False
        for c in node.children_by_field_name("name"):
            if c.type == "aliased_import":
                names.append((_t(c.child_by_field_name("name")), _t(c.child_by_field_name("alias"))))
            else:
                names.append((_t(c), _t(c).split(".")[-1]))
        if any(c.type == "wildcard_import" for c in node.named_children):
            star = True
        self.r.imports.append(ImportRef(spec, tuple(names), star=star, line=_line(node)))

    def _decorator_name(self, deco: Node) -> tuple[str, Node | None]:
        expr = deco.named_children[0] if deco.named_children else None
        if expr is not None and expr.type == "call":
            return _t(expr.child_by_field_name("function")), expr
        return _t(expr), None

    def _class(self, node: Node, decorators: list[Node]) -> None:
        name = _t(node.child_by_field_name("name"))
        body = node.child_by_field_name("body")
        supers = node.child_by_field_name("superclasses")
        bases = [
            _t(a).split(".")[-1]
            for a in (supers.named_children if supers is not None else [])
            if a.type in ("identifier", "attribute")
        ]
        sym = SymbolDef(
            name,
            name,
            "class",
            _line(node),
            _end(node),
            _compact(f"class {name}({', '.join(bases)})"),
            _docstring(body),
            not name.startswith("_"),
            extends=bases,
            decorators=[self._decorator_name(d)[0].split(".")[-1] for d in decorators],
            loc=_end(node) - _line(node) + 1,
        )
        self.r.symbols.append(sym)
        for base in bases:
            self.r.refs.append(RefUse(name, base, "extends", _line(node)))
        hints: dict[str, Any] = {}
        if "BaseModel" in bases:
            hints["validator"] = "pydantic"
        if "Model" in bases and any("models.Model" in _t(a) for a in (supers.named_children if supers else [])):
            hints["table"] = name.lower()
        if body is None:
            return
        for stmt in body.named_children:
            inner = stmt.child_by_field_name("definition") if stmt.type == "decorated_definition" else stmt
            decos = (
                [d for d in stmt.named_children if d.type == "decorator"] if stmt.type == "decorated_definition" else []
            )
            if inner is not None and inner.type == "function_definition":
                self._function(inner, sym, decos)
            elif _unwrap(stmt).type == "assignment":
                assign = _unwrap(stmt)
                if True:
                    left, right = assign.child_by_field_name("left"), assign.child_by_field_name("right")
                    if _t(left) == "__tablename__" and right is not None:
                        hints["table"] = _t(right).strip("'\"")
                    tname = _type_name(assign.child_by_field_name("type"))
                    if left is not None and left.type == "identifier" and tname:
                        sym.field_types[_t(left)] = tname
        sym.hints.update(hints)

    def _params(self, params: Node | None) -> dict[str, str]:
        types: dict[str, str] = {}
        if params is None:
            return types
        for p in params.named_children:
            if p.type in ("typed_parameter", "typed_default_parameter"):
                name_node = p.child_by_field_name("name") or next(
                    (c for c in p.named_children if c.type == "identifier"), None
                )
                tname = _type_name(p.child_by_field_name("type"))
                if name_node is not None and tname:
                    types[_t(name_node)] = tname
        return types

    def _function(self, node: Node, cls: SymbolDef | None, decorators: list[Node]) -> None:
        name = _t(node.child_by_field_name("name"))
        params = node.child_by_field_name("parameters")
        types = self._params(params)
        body = node.child_by_field_name("body")
        ret = node.child_by_field_name("return_type")
        if cls is not None and name == "__init__":
            for tname in types.values():
                cls.ctor_types.append(tname)
                self.r.refs.append(RefUse(cls.qualname, tname, "constructor", _line(node)))
            if body is not None:
                for n in _walk(body):
                    if n.type != "assignment":
                        continue
                    left, right = n.child_by_field_name("left"), n.child_by_field_name("right")
                    if left is None or left.type != "attribute" or _t(left.child_by_field_name("object")) != "self":
                        continue
                    attr = _t(left.child_by_field_name("attribute"))
                    if right is not None and right.type == "identifier" and _t(right) in types:
                        cls.field_types[attr] = types[_t(right)]
                    elif right is not None and right.type == "call":
                        fn = _t(right.child_by_field_name("function")).split(".")[-1]
                        if fn[:1].isupper():
                            cls.field_types[attr] = fn
                self._scan(body, cls.qualname, dict(types))
            return
        qual = f"{cls.qualname}.{name}" if cls else name
        sym = SymbolDef(
            name,
            qual,
            "method" if cls else "function",
            _line(node),
            _end(node),
            _compact(f"def {name}{_t(params)}" + (f" -> {_t(ret)}" if ret is not None else "")),
            _docstring(body),
            not name.startswith("_") and (cls is None or cls.exported),
            parent=cls.qualname if cls else None,
            param_types=types,
            loc=_end(node) - _line(node) + 1,
        )
        sym.decorators = [self._decorator_name(d)[0].split(".")[-1] for d in decorators]
        if cls is not None:
            cls.methods.append(name)
        self.r.symbols.append(sym)
        for tname in types.values():
            self.r.refs.append(RefUse(qual, tname, "type", _line(node)))
        for deco in decorators:
            self._route_decorator(deco, sym)
        if body is not None:
            scope = {**(cls.field_types if cls else {}), **types}
            self._scan(body, qual, scope, sym)

    def _route_decorator(self, deco: Node, handler: SymbolDef) -> None:
        fname, call = self._decorator_name(deco)
        if call is None or "." not in fname:
            return
        verb = fname.rsplit(".", 1)[1].lower()
        args = call.child_by_field_name("arguments")
        if verb not in HTTP_VERBS or args is None or not args.named_children:
            return
        first = args.named_children[0]
        if first.type != "string":
            return
        path = _t(first).strip("rbuRBU").strip("'\"")
        method = verb.upper() if verb not in ("route", "api_route") else "ANY"
        qual = f"route:{method} {path}"
        self.r.symbols.append(
            SymbolDef(
                f"{method} {path}",
                qual,
                "route",
                _line(deco),
                handler.line_end,
                _compact(_t(deco)),
                handler.doc,
                False,
                hints={
                    "http_method": method,
                    "http_path": path,
                    "handler": handler.qualname,
                    "validated": bool(handler.param_types),
                    "router": fname.split(".")[0],
                },
                loc=handler.loc,
            )
        )

    def _module_assignment(self, assign: Node) -> None:
        left, right = assign.child_by_field_name("left"), assign.child_by_field_name("right")
        if left is None or left.type != "identifier" or right is None:
            return
        name = _t(left)
        hints: dict[str, Any] = {}
        if right.type == "call":
            fn = _t(right.child_by_field_name("function"))
            if fn.split(".")[-1][:1].isupper():
                hints["instance_of"] = fn.split(".")[-1]
            if fn.split(".")[-1] in ("APIRouter", "Blueprint", "FastAPI", "Flask"):
                hints["router"] = True
        if hints:
            self.r.symbols.append(
                SymbolDef(
                    name,
                    name,
                    "const",
                    _line(assign),
                    _end(assign),
                    _compact(_t(assign), 120),
                    None,
                    not name.startswith("_"),
                    hints=hints,
                )
            )
        self._scan(right, name if hints else None, {})

    def _scan(self, root: Node, owner: str | None, scope: dict[str, str], sym: SymbolDef | None = None) -> None:
        for node in _walk(root):
            if node.type == "call":
                fn = node.child_by_field_name("function")
                args = node.child_by_field_name("arguments")
                if fn is None:
                    continue
                if fn.type == "identifier":
                    self.r.calls.append(CallRef(owner, None, _t(fn), _line(node)))
                elif fn.type == "attribute":
                    obj = fn.child_by_field_name("object")
                    method = _t(fn.child_by_field_name("attribute"))
                    rec = _receiver(obj) if obj is not None else None
                    self.r.calls.append(CallRef(owner, rec, method, _line(node)))
                    if (
                        method in ("query", "select", "insert", "update", "delete")
                        and args is not None
                        and args.named_children
                    ):
                        first = args.named_children[0]
                        if first.type == "identifier":
                            op = "read" if method in ("query", "select") else "write"
                            self.r.tables.append(TableAccess(owner, _t(first), op, _line(node)))
                if args is not None:
                    for a in args.named_children:
                        if a.type == "identifier":
                            self.r.refs.append(RefUse(owner, _t(a), "reference", _line(a)))
            elif node.type == "assignment" and sym is not None:
                left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
                if left is not None and left.type == "identifier" and right is not None and right.type == "call":
                    fn = _t(right.child_by_field_name("function")).split(".")[-1]
                    if fn[:1].isupper():
                        sym.local_types[_t(left)] = fn
