"""The teamapp-ts fixture end to end without a database: extraction, detectors, rules, golden snapshot."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from agent_factory.auditor.constraints import evaluate, infer_constraints
from agent_factory.auditor.graphview import GraphView
from agent_factory.auditor.patterns import detect_patterns
from agent_factory.auditor.pipeline import extract
from agent_factory.config import parse_config
from tests.fixtures.build_teamapp import build_teamapp

CONFIG = parse_config({"project": {"id": "teamapp", "name": "TeamApp", "languages": ["typescript"]}})


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    root = build_teamapp(tmp_path_factory.mktemp("fx") / "teamapp")
    frag, docs, walked = extract(root, CONFIG)
    return root, frag, docs, walked


def _short(uid: str) -> str:
    return uid.split("#", 1)[-1]


def test_walk_skips_env_and_parses_everything(extracted):
    _, frag, docs, walked = extracted
    assert walked.skipped.get("secret_file") == 1
    assert frag.stats["parsed"] == 28 and frag.stats["files_with_parse_errors"] == 0
    assert len(docs) == 7 and sum(1 for d in docs if d.adr) == 4


def test_roles_and_edges(extracted):
    _, frag, _, _ = extracted
    roles = Counter(r for s in frag.symbols for r in s.roles)
    assert roles["Service"] == 6 and roles["Repository"] == 3 and roles["Controller"] == 3
    assert roles["Model"] == 4 and roles["Route"] == 10 and roles["Validator"] == 6 and roles["Integration"] == 1
    edges = {(e.rel, _short(e.src), _short(e.dst)) for e in frag.edges}
    assert ("USES", "TeamController", "TeamService") in edges
    assert ("USES", "PasswordResetService", "TokenService") in edges
    assert ("USES", "UserController", "UserRepository") in edges  # the deliberate violation
    assert ("ACCESSES", "TokenRepository.insert", "tokens") in edges
    assert ("HANDLES", "route:PATCH /me", "UserController.updateProfile") in edges
    assert ("CALLS", "PasswordResetService.resetPassword", "TokenService.consume") in edges
    assert frag.external_deps.keys() >= {"express", "zod", "drizzle-orm", "vitest"}


def test_patterns_and_violations(extracted):
    _, frag, _, _ = extracted
    patterns = {p.key: p for p in detect_patterns(GraphView.from_fragment(frag))}
    ctl = patterns["controllers-delegate-to-services"]
    assert len(ctl.support) == 2 and [_short(v) for v in ctl.violations] == ["UserController"]
    assert [_short(v) for v in patterns["route-input-validation"].violations] == ["route:PATCH /me"]
    token = patterns["lifecycle-token-tokenservice"]
    assert {_short(u) for u in token.support} == {"TokenService", "PasswordResetService", "EmailVerificationService"}
    assert patterns["errors-extend-base"].title == "Errors extend AppError"
    assert len(patterns["naming-service"].support) == 6
    assert "vitest" in patterns["test-layout"].title


def test_inferred_constraints(extracted):
    _, frag, _, _ = extracted
    view = GraphView.from_fragment(frag)
    rules = {c.key: c for c in infer_constraints(view, detect_patterns(view))}
    forbid = rules["forbid-dependency-controller-repository"]
    assert [_short(v) for v in forbid.violations] == ["UserController"]
    assert rules["single-validation-library"].rule == {"category": "validation", "allowed": ["zod"]}
    assert rules["placement-service"].rule["glob"] == "src/services/*.service.ts"
    # A second validation library is caught by the rule evaluator.
    view.files["src/new.ts"] = {"uid": "teamapp:file:src/new.ts", "is_test": False, "external_imports": ["joi"]}
    _, violations = evaluate(view, "forbid_external_dep", {"category": "validation", "allowed": ["zod"]})
    assert ("teamapp:file:src/new.ts", "joi") in violations


def test_fragment_golden_snapshot(extracted, snapshot):
    """Any change to what the auditor extracts shows up as a readable diff here."""
    _, frag, _, _ = extracted
    assert frag.normalized() == snapshot


def test_no_function_bodies_or_secrets_in_symbol_properties(extracted):
    from agent_factory.common.redact import contains_secret

    _, frag, docs, _ = extracted
    for s in frag.symbols:
        for key, value in s.props.items():
            if isinstance(value, str):
                assert not contains_secret(value), (s.uid, key)
        assert "await this.tokens.insert" not in s.props["card"]
    assert all(not contains_secret(c.text) for d in docs for c in d.chunks)
    email_client = next(s for s in frag.symbols if s.props["name"] == "EmailClient")
    assert "hunter22" not in (email_client.props["doc"] or "")


def test_docs_extraction(extracted):
    _, _, docs, _ = extracted
    adrs = {d.adr.adr_id: d.adr for d in docs if d.adr}
    assert adrs["ADR-003"].status.value == "superseded" and adrs["ADR-003"].superseded_by == ["ADR-004"]
    assert adrs["ADR-004"].supersedes == ["ADR-003"] and adrs["ADR-002"].status.value == "validated"
    rules = [r.text for d in docs for r in d.rules]
    assert "Never call repositories from controllers. Controllers talk to services only." in rules


def test_python_adapter_used_for_python_repos(tmp_path: Path):
    from tests.conftest import make_repo

    root = make_repo(
        tmp_path / "py",
        {
            "app/services/token_service.py": "class TokenService:\n    def create(self):\n        pass\n",
            "app/__init__.py": "",
            "app/services/__init__.py": "",
        },
    )
    frag, _, _ = extract(root, parse_config({"project": {"id": "py", "name": "py", "languages": ["python"]}}))
    assert any(s.props["name"] == "TokenService" and s.roles == ["Service"] for s in frag.symbols)
