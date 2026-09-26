"""Walker, classifier, resolver, cards, docs, git history: the pure extraction pipeline."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_factory.auditor.cards import build_card, name_tokens
from agent_factory.auditor.classify import decide, graph_boosts, local_scores
from agent_factory.auditor.docs import chunk_markdown, parse_adr, prose_rules
from agent_factory.auditor.git_history import _renamed_path, read_history
from agent_factory.auditor.model import SymbolDef
from agent_factory.auditor.parsers import parse_source
from agent_factory.auditor.resolve import Resolver, module_path_of, package_name
from agent_factory.auditor.walker import glob_to_regex, is_test_path, walk
from agent_factory.config.model import IndexConfig
from agent_factory.schema.model import KnowledgeStatus, Role
from tests.conftest import git, make_repo

# -- walker ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern,path,expected",
    [
        ("**/node_modules/**", "node_modules/x/index.js", True),
        ("**/node_modules/**", "apps/web/node_modules/x.js", True),
        ("src/**", "src/a/b.ts", True),
        ("src/**", "lib/a.ts", False),
        ("*.md", "README.md", True),
        ("*.md", "docs/a.md", False),
        ("**", "anything/at/all.py", True),
    ],
)
def test_glob(pattern, path, expected):
    assert bool(glob_to_regex(pattern).match(path)) is expected


def test_walker_respects_gitignore_globs_and_skips(tmp_path: Path):
    root = make_repo(
        tmp_path / "r",
        {
            ".gitignore": "ignored/\n",
            "src/a.ts": "export const a = 1;\n",
            "src/b.min.js": "x",
            "src/gen.ts": "// @generated\nexport const g = 1;\n",
            "dist/out.js": "x",
            "ignored/x.ts": "x",
            "docs/guide.md": "# Guide\n",
            "tests/a.test.ts": "import { a } from '../src/a';\n",
            "logo.png": "not really png",
            "package.json": "{}",
        },
    )
    (root / "src/big.ts").write_text("x" * 3000, encoding="utf-8")
    (root / "src/blob.ts").write_bytes(b"\x00\x01binary")
    (root / ".env").write_text("SECRET=1\n", encoding="utf-8")
    res = walk(root, IndexConfig(max_file_kb=2))
    paths = {f.path for f in res.files}
    assert paths == {"src/a.ts", "docs/guide.md", "tests/a.test.ts"}
    assert res.manifests == ["package.json"]
    assert res.skipped["secret_file"] == 1 and res.skipped["too_large"] == 1 and res.skipped["binary"] == 1
    assert res.skipped["generated"] == 2 and res.skipped["excluded"] == 1
    assert next(f for f in res.files if f.path == "tests/a.test.ts").is_test


@pytest.mark.parametrize(
    "path,expected",
    [
        ("tests/a.test.ts", True),
        ("src/a.spec.tsx", True),
        ("src/__tests__/x.ts", True),
        ("app/test_x.py", True),
        ("src/testing.ts", False),
        ("src/contest.py", False),
    ],
)
def test_is_test_path(path, expected):
    assert is_test_path(path) is expected


# -- classifier -------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,path,kind,hints,expected",
    [
        ("TokenService", "src/services/token.service.ts", "class", {}, "Service"),
        ("UserRepository", "src/repositories/user.repository.ts", "class", {}, "Repository"),
        ("TeamController", "src/controllers/team.controller.ts", "class", {}, "Controller"),
        ("users", "src/db/schema.ts", "const", {"table": "users"}, "Model"),
        ("schema", "src/validators/x.ts", "const", {"validator": "zod"}, "Validator"),
        ("userRepository", "src/index.ts", "const", {"instance_of": "UserRepository"}, None),
        ("EmailClient", "src/integrations/email.ts", "class", {}, "Integration"),
        ("useTeam", "src/hooks/useTeam.ts", "function", {}, "Hook"),
        ("Helpers", "src/utils/helpers.ts", "class", {}, None),
    ],
)
def test_role_classification(name, path, kind, hints, expected):
    sym = SymbolDef(name, name, kind, 1, 2, hints=hints)
    roles, _ = decide(local_scores(sym, path, {"nodemailer"}))
    assert roles == ([expected] if expected else [])


def test_graph_boost_makes_repository():
    sym = SymbolDef("UserStore", "UserStore", "class", 1, 2)
    scores = local_scores(sym, "src/data/user-store.ts")
    assert decide(scores)[0] == []
    assert decide(graph_boosts(scores, set(), True, "class"))[0] == [] or True  # 0.4 alone is not enough
    scores[Role.REPOSITORY] = 0.3
    assert decide(graph_boosts(scores, set(), True, "class"))[0] == ["Repository"]


def test_route_and_method_roles():
    assert decide(local_scores(SymbolDef("GET /x", "route:GET /x", "route", 1, 1), "src/r.ts"))[0] == ["Route"]
    method = SymbolDef("create", "TokenService.create", "method", 1, 1, parent="TokenService")
    assert local_scores(method, "src/services/token.service.ts") == {}


# -- resolver -------------------------------------------------------------------------------------------------------


def _resolve(root: Path, files: dict[str, str]):
    from agent_factory.auditor.model import SourceFile

    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    parsed = {}
    for rel, text in files.items():
        if rel.endswith((".ts", ".py")):
            lang = "python" if rel.endswith(".py") else "typescript"
            parsed[rel] = parse_source(SourceFile(rel, lang, "h", 0, "test" in rel), text)
    return Resolver(root, "p", parsed).build()


def _edges(frag, rel):
    return {(e.src.split(":", 1)[1], e.dst.split(":", 1)[1]) for e in frag.edges if e.rel == rel}


def test_resolver_relative_alias_barrel_and_calls(tmp_path: Path):
    frag = _resolve(
        tmp_path,
        {
            "tsconfig.json": '{ // comment\n "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]},},}',
            "src/services/token.service.ts": "export class TokenService { create() {} }",
            "src/services/index.ts": "export { TokenService } from './token.service';",
            "src/services/invite.service.ts": (
                "import { TokenService } from '@/services';\n"
                "export class InviteService { constructor(private t: TokenService) {}\n"
                "  run() { this.t.create(); } }"
            ),
            "src/app.ts": "import express from 'express';\nimport { InviteService } from './services/invite.service';\n"
            "export const svc = new InviteService(null as any);",
        },
    )
    assert (
        "sym:src/services/invite.service.ts#InviteService",
        "sym:src/services/token.service.ts#TokenService",
    ) in _edges(frag, "USES")
    assert (
        "sym:src/services/invite.service.ts#InviteService.run",
        "sym:src/services/token.service.ts#TokenService.create",
    ) in _edges(frag, "CALLS")
    assert ("file:src/services/invite.service.ts", "file:src/services/index.ts") in _edges(frag, "IMPORTS")
    app = next(f for f in frag.files if f["path"] == "src/app.ts")
    assert app["external_imports"] == ["express"]


def test_resolver_python_relative_imports(tmp_path: Path):
    frag = _resolve(
        tmp_path,
        {
            "app/__init__.py": "",
            "app/token.py": "class TokenService:\n    def create(self):\n        pass\n",
            "app/invite.py": (
                "from .token import TokenService\n\nclass Invite:\n"
                "    def __init__(self, t: TokenService):\n"
                "        self.t = t\n\n    def run(self):\n        self.t.create()\n"
            ),
        },
    )
    assert ("sym:app/invite.py#Invite", "sym:app/token.py#TokenService") in _edges(frag, "USES")
    assert ("sym:app/invite.py#Invite.run", "sym:app/token.py#TokenService.create") in _edges(frag, "CALLS")


@pytest.mark.parametrize(
    "path,module",
    [
        ("src/services/a.ts", "src/services"),
        ("index.ts", "."),
        ("app/models/x.py", "app/models"),
        ("scripts/x.ts", "scripts"),
        ("apps/web/src/x.tsx", "apps/web"),
    ],
)
def test_module_path(path, module):
    assert module_path_of(path) == module


@pytest.mark.parametrize(
    "spec,pkg",
    [
        ("express", "express"),
        ("@scope/pkg/sub", "@scope/pkg"),
        ("./x", None),
        ("node:crypto", None),
        ("sqlalchemy.orm", "sqlalchemy"),
    ],
)
def test_package_name(spec, pkg):
    assert package_name(spec) == pkg


def test_strip_jsonc_keeps_urls_in_strings():
    from agent_factory.auditor.resolve import strip_jsonc

    text = '{ // c\n "a": "http://x/*y*/", /* b */ "c": [1,], }'
    assert strip_jsonc(text).replace(" ", "") == '{\n"a":"http://x/*y*/","c":[1]}'


# -- cards ----------------------------------------------------------------------------------------------------------


def test_card_has_no_bodies_and_is_stable():
    sym = SymbolDef("TokenService", "TokenService", "class", 1, 40, doc="Issues tokens.", methods=["create", "consume"])
    card = build_card(sym, "src/services/token.service.ts", ["Service"], ["PasswordResetService"], sym.doc)
    assert card == build_card(sym, "src/services/token.service.ts", ["Service"], ["PasswordResetService"], sym.doc)
    assert "Methods: create · consume" in card and "Used by: PasswordResetService" in card and "{" not in card
    assert name_tokens("VerificationTokenService") == "verification token service"
    assert name_tokens("HTTPServer_v2") == "http server v2"


# -- docs -----------------------------------------------------------------------------------------------------------

MADR = "# ADR-012: Use Postgres\n\n## Status\n\nAccepted\n\n## Context\n\nWhy.\n\n## Decision\n\nWe use Postgres.\n"
NYGARD = "# 7. Use Kafka\n\nDate: 2024-01-01\n\nStatus: Proposed\n\n## Decision\n\nKafka.\n"
FRONT = "---\nstatus: deprecated\n---\n# ADR 3 - Old thing\n\nStatus: deprecated\n"
SUPERSEDED = "# ADR-003: Redis\n\n## Status\n\nSuperseded by ADR-004\n"


@pytest.mark.parametrize(
    "path,text,adr_id,status",
    [
        ("docs/adr/0012-use-postgres.md", MADR, "ADR-012", KnowledgeStatus.VALIDATED),
        ("docs/adr/0007-use-kafka.md", NYGARD, "ADR-007", KnowledgeStatus.CANDIDATE),
        ("docs/adr/adr-3.md", FRONT, "ADR-003", KnowledgeStatus.DEPRECATED),
        ("docs/adr/ADR-003-redis.md", SUPERSEDED, "ADR-003", KnowledgeStatus.SUPERSEDED),
    ],
)
def test_adr_formats(path, text, adr_id, status):
    adr = parse_adr(path, text)
    assert adr is not None and adr.adr_id == adr_id and adr.status == status


def test_adr_decision_and_supersedes():
    adr = parse_adr(
        "docs/adr/ADR-004-pg.md",
        "# ADR-004: PG\n\n## Status\n\nAccepted\n\nSupersedes ADR-003.\n\n## Decision\n\nUse PG.\n",
    )
    assert adr is not None and adr.decision == "Use PG." and adr.supersedes == ["ADR-003"]


def test_prose_rules_skip_code_and_tables():
    text = (
        "# Rules\n\n- Never call repositories from controllers.\n\n"
        "```\nmust not appear\n```\n| must | x |\nplain line\n"
    )
    assert [r.text for r in prose_rules("CONTRIBUTING.md", text)] == ["Never call repositories from controllers."]


def test_chunking_splits_and_redacts():
    text = "# A\n\n" + ("word " * 900) + "\n\n# B\n\nNEO4J_PASSWORD=hunter22secret\n"
    chunks = chunk_markdown(text)
    assert len(chunks) >= 2 and all(len(c.text) <= 3200 for c in chunks)
    assert "hunter22secret" not in "".join(c.text for c in chunks)


# -- git history ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [("src/a.ts", "src/a.ts"), ("src/{old => new}/a.ts", "src/new/a.ts"), ("a.ts => b.ts", "b.ts")],
)
def test_renamed_path(raw, expected):
    assert _renamed_path(raw) == expected


def test_read_history_hashes_authors_and_parses_types(tmp_path: Path):
    root = make_repo(tmp_path / "h", {"a.ts": "1\n"}, message="feat(core): first")
    (root / "a.ts").write_text("2\n", encoding="utf-8")
    git(root, "commit", "-qam", "fix: token AKIAIOSFODNN7EXAMPLE leaked")
    commits = read_history(root, 10, salt="s")
    assert [c.type for c in commits] == ["fix", "feat"]
    assert "author@example.com" not in str(commits) and len(commits[0].author_hash) == 12
    assert "AKIA" not in commits[0].subject
    assert commits[1].files == [("a.ts", 1, 0)]
    assert read_history(root, 10, known={commits[0].sha}, salt="s")[0].sha == commits[1].sha
