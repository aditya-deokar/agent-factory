"""Language adapters, table-driven over small snippets."""

from __future__ import annotations

import pytest

from agent_factory.auditor.model import SourceFile
from agent_factory.auditor.parsers import parse_source


def ts(code: str, path: str = "src/x.ts"):
    lang = "javascript" if path.endswith((".js", ".jsx")) else "typescript"
    pf = parse_source(SourceFile(path, lang, "h", len(code)), code)
    assert pf is not None and pf.errors == 0
    return pf


def py(code: str, path: str = "app/x.py"):
    pf = parse_source(SourceFile(path, "python", "h", len(code)), code)
    assert pf is not None
    return pf


def sym(pf, qualname):
    return next(s for s in pf.symbols if s.qualname == qualname)


# -- TypeScript ---------------------------------------------------------------------------------------------------


def test_class_methods_di_and_doc():
    pf = ts(
        """
import { TokenService } from './token.service';
/** Sends invites. */
export class InviteService extends BaseService implements Svc {
  private cache = new Map();
  constructor(private readonly tokens: TokenService, email: EmailService) { super(); }
  /** Create one. */
  async create(teamId: string): Promise<string> { return this.tokens.create(teamId); }
}
"""
    )
    cls = sym(pf, "InviteService")
    assert cls.kind == "class" and cls.exported and cls.doc == "Sends invites."
    assert cls.extends == ["BaseService"] and cls.implements == ["Svc"]
    assert cls.ctor_types == ["TokenService", "EmailService"]
    assert cls.field_types["tokens"] == "TokenService" and "email" not in cls.field_types  # not a param property
    assert cls.methods == ["create"]
    method = sym(pf, "InviteService.create")
    assert method.parent == "InviteService" and method.doc == "Create one." and "teamId: string" in method.signature
    assert ("InviteService.create", "this.tokens", "create") in {
        (c.from_qualname, c.receiver, c.method) for c in pf.calls
    }
    assert ("InviteService", "TokenService", "constructor") in {(r.from_qualname, r.target, r.via) for r in pf.refs}


@pytest.mark.parametrize(
    "code,expected",
    [
        ("import { A, B as C } from './a';", {"source": "./a", "names": (("A", "A"), ("B", "C"))}),
        ("import D from 'lib';", {"source": "lib", "default": "D"}),
        ("import * as schema from '../db/schema';", {"source": "../db/schema", "namespace": "schema"}),
        ("import type { R } from 'express';", {"source": "express", "type_only": True}),
        ("export { X } from './x';", {"source": "./x", "reexport": True, "names": (("X", "X"),)}),
        ("export * from './all';", {"source": "./all", "reexport": True, "star": True}),
    ],
)
def test_imports(code, expected):
    imp = ts(code).imports[0]
    for key, value in expected.items():
        assert getattr(imp, key) == value


def test_exported_function_arrow_const_and_default():
    pf = ts(
        """
export function f(a: Foo): Bar { return g(a); }
export const h = async (x: Baz) => new Qux(x);
function internal() {}
export default function Page() { return null; }
"""
    )
    assert sym(pf, "f").exported and sym(pf, "f").param_types == {"a": "Foo"}
    assert sym(pf, "h").kind == "function" and sym(pf, "h").exported
    assert not sym(pf, "internal").exported
    assert sym(pf, "Page").exported
    assert ("h", "Qux", "new") in {(r.from_qualname, r.target, r.via) for r in pf.refs}


def test_export_clause_marks_symbols_exported():
    pf = ts("class A {}\nfunction b() {}\nexport { A, b };")
    assert sym(pf, "A").exported and sym(pf, "b").exported


def test_express_routes_and_validation_flag():
    pf = ts(
        """
export function routes(controller: TeamController) {
  const router = Router();
  router.post('/teams', validate(createTeamSchema), (req, res) => controller.create(req, res));
  router.get('/teams/:id', (req, res) => controller.get(req, res));
  return router;
}
"""
    )
    post, get = sym(pf, "route:POST /teams"), sym(pf, "route:GET /teams/:id")
    assert post.kind == "route" and post.hints["validated"] is True and get.hints["validated"] is False
    assert post.param_types == {"controller": "TeamController"}  # enclosing scope flows into the route
    calls = {(c.from_qualname, c.receiver, c.method) for c in pf.calls}
    assert ("route:POST /teams", "controller", "create") in calls


def test_drizzle_tables_and_access():
    pf = ts(
        """
export const users = pgTable('users', { id: text('id') });
export class UserRepository {
  async find(id: string) { return db.select().from(users).where(eq(users.id, id)); }
  async add(u: User) { await db.insert(users).values(u); }
}
"""
    )
    assert sym(pf, "users").hints == {"table": "users"}
    tables = {(t.from_qualname, t.table, t.op) for t in pf.tables}
    assert ("UserRepository.find", "users", "read") in tables
    assert ("UserRepository.add", "users", "write") in tables


def test_zod_validator_and_instances():
    pf = ts("export const s = z.object({ a: z.string() });\nconst repo = new UserRepository();")
    assert sym(pf, "s").hints == {"validator": "zod"}
    assert sym(pf, "repo").hints == {"instance_of": "UserRepository"}


def test_jsx_component_and_hook():
    pf = ts(
        """
export function useTeam(id: string) { return useQuery(id); }
export function TeamCard({ team }: Props) { return <Card title={team.name}><Avatar /></Card>; }
""",
        path="src/TeamCard.tsx",
    )
    assert sym(pf, "TeamCard").returns_jsx and not sym(pf, "useTeam").returns_jsx
    assert ("TeamCard", "Card", "reference") in {(r.from_qualname, r.target, r.via) for r in pf.refs}


def test_decorators_on_exported_class():
    pf = ts("@Injectable()\nexport class A { constructor(private b: B) {} }")
    assert sym(pf, "A").decorators == ["Injectable"]


def test_javascript_file_parses():
    pf = ts("export class A { run() { return new B(); } }", path="src/a.js")
    assert sym(pf, "A.run").kind == "method"


# -- Python -------------------------------------------------------------------------------------------------------


def test_python_classes_di_routes_models():
    pf = py(
        '''
from fastapi import APIRouter
from .services.token import TokenService
router = APIRouter()

class User(Base):
    """A user."""
    __tablename__ = "users"

class InviteIn(BaseModel):
    email: str

class InviteService:
    def __init__(self, tokens: TokenService):
        self.tokens = tokens
        self.cache = Cache()

    def create(self, team_id: str) -> str:
        return self.tokens.create(team_id)

@router.post("/invites")
def create_invite(body: InviteIn):
    return svc.create(body.email)
'''
    )
    assert sym(pf, "User").hints == {"table": "users"} and sym(pf, "User").doc == "A user."
    assert sym(pf, "InviteIn").hints == {"validator": "pydantic"}
    svc = sym(pf, "InviteService")
    assert svc.ctor_types == ["TokenService"] and svc.field_types == {"tokens": "TokenService", "cache": "Cache"}
    route = sym(pf, "route:POST /invites")
    assert route.hints["handler"] == "create_invite"
    assert sym(pf, "router").hints["router"] is True
    assert ("InviteService.create", "self.tokens", "create") in {
        (c.from_qualname, c.receiver, c.method) for c in pf.calls
    }
    assert [(i.source, i.names) for i in pf.imports][1] == (".services.token", (("TokenService", "TokenService"),))
