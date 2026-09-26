"""Phase 5 pure logic: query understanding, fusion, budget, reuse scoring, Text2Cypher guard, rendering."""

from __future__ import annotations

import pytest

from agent_factory.context.ask import UnsafeQuery, ask, clean_cypher, ensure_limit, static_check
from agent_factory.context.pack import (
    ContextPack,
    KnowledgeItem,
    SymbolItem,
    apply_budget,
    count_tokens,
    render_markdown,
)
from agent_factory.context.query import singular, tokenize, understand
from agent_factory.context.retrievers import Hit, lucene_query, rrf
from agent_factory.context.reuse import (
    ProposedAbstraction,
    ReuseCandidate,
    ReuseReport,
    core_tokens,
    inferred_role,
    maturity,
    method_overlap,
    name_similarity,
    normalize_verb,
    render_reuse,
    verdict_for,
)
from agent_factory.mcp.server import fit

# -- query ----------------------------------------------------------------------------------------------------------


def test_tokenize_camel_snake_plural_and_stopwords():
    assert tokenize("Add team invitations to the TeamService and user_profiles") == [
        "team",
        "invitation",
        "service",
        "user",
        "profile",
    ]
    assert singular("policies") == "policy" and singular("status") == "status" and singular("class") == "class"


def test_synonym_expansion_and_identifiers():
    q = understand("Add team invitations for OrgAdmins")
    assert q.terms[:2] == ["team", "invitation"]
    assert {"token", "email", "workspace"} <= set(q.expanded)
    assert q.identifiers == ["OrgAdmins"]
    assert understand("Add team invitations", expand=False).expanded == []


# -- fusion ---------------------------------------------------------------------------------------------------------


def test_rrf_rewards_agreement_and_keeps_reasons():
    fused = rrf(
        {
            "vector": [Hit("a", 0.9, "vector", "v"), Hit("b", 0.8, "vector", "v")],
            "fulltext": [Hit("b", 3.0, "fulltext", "f"), Hit("c", 2.0, "fulltext", "f")],
        }
    )
    assert [f.uid for f in fused] == ["b", "a", "c"]
    assert fused[0].sources == {"vector", "fulltext"} and len(fused[0].why) == 2


def test_rrf_is_stable_on_ties_and_weighted():
    fused = rrf({"x": [Hit("z", 1, "x", ""), Hit("y", 1, "x", "")], "w": [Hit("y", 1, "w", "")]}, weights={"w": 0.0})
    assert [f.uid for f in fused] == ["z", "y"]


def test_lucene_query_escapes_and_prefixes():
    assert lucene_query(["token", "a+b", "id"]) == "token* OR a\\+b OR id"
    assert lucene_query(["users:all"]) == "users\\:all*"


# -- budget & rendering ---------------------------------------------------------------------------------------------


def _k(i: int, status: str = "validated", kind: str = "constraint") -> KnowledgeItem:
    return KnowledgeItem(
        uid=f"k{i}",
        kind=kind,
        title=f"Rule number {i} " + "word " * 20,
        claim="c",
        status=status,
        confidence=0.9,
        support=5,
    )


def _pack() -> ContextPack:
    return ContextPack(
        request="Add team invitations",
        project_id="p",
        reusable=[
            SymbolItem(
                uid=f"s{i}",
                name=f"Service{i}",
                kind="class",
                role="Service",
                path=f"src/s{i}.ts",
                line=3,
                methods=["create", "validate"],
                used_by=["A", "B"],
            )
            for i in range(20)
        ],
        constraints=[_k(i) for i in range(6)],
        patterns=[_k(i, kind="pattern") for i in range(10)],
        unverified=[_k(i, "candidate", "pattern") for i in range(10)],
    )


def test_budget_never_cuts_constraints_and_lists_cuts():
    pack = apply_budget(_pack(), 300)
    assert len(pack.constraints) == 6
    assert pack.budget.cut and any(c.startswith("reusable") for c in pack.budget.cut)
    assert pack.budget.per_section["constraints"] > 0


def test_unused_budget_flows_forward():
    small = apply_budget(_pack(), 1200).reusable
    pack = _pack()
    pack.constraints = []
    assert len(apply_budget(pack, 1200).reusable) > len(small)


def test_generous_budget_keeps_everything():
    pack = apply_budget(_pack(), 100_000)
    assert len(pack.reusable) == 20 and pack.budget.cut == [] and pack.budget.used <= 100_000


def test_count_tokens_positive():
    assert count_tokens("TokenService create validate") >= 3


def test_render_markdown_snapshot(snapshot):
    pack = apply_budget(_pack(), 900)
    assert render_markdown(pack) == snapshot


# -- reuse scoring --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "method,verb",
    [
        ("createToken", "create"),
        ("issue", "create"),
        ("verify", "validate"),
        ("confirm", "validate"),
        ("redeemCode", "consume"),
        ("revoke", "expire"),
        ("dispatch", "send"),
        ("exportCsv", "export"),
    ],
)
def test_verb_normalization(method, verb):
    assert normalize_verb(method) == verb


def test_name_similarity():
    assert name_similarity("InvitationTokenService", "TokenService") == 0.9
    assert name_similarity("MailerService", "EmailService") == 1.0  # synonym, not spelling
    assert name_similarity("InvitationTokenService", "EmailService") == 0.0
    assert name_similarity("MembershipService", "TeamService", "Add team members") == 0.8
    assert core_tokens("TokenService") == {"token"} and core_tokens("Service") == {"service"}


def test_method_overlap_is_coverage_of_the_proposal():
    assert method_overlap(["create", "validate", "expire"], ["create", "validate", "consume", "expire"]) == 1.0
    assert method_overlap(["charge"], ["create", "validate"]) == 0.0
    assert method_overlap([], ["create"]) == 0.0


@pytest.mark.parametrize("score,verdict", [(0.7, "reuse"), (0.69, "extend"), (0.5, "extend"), (0.49, "new_ok")])
def test_verdict_thresholds(score, verdict):
    assert verdict_for(score) == verdict


def test_maturity_and_role():
    assert maturity(0) == 0.0 and maturity(8) == 1.0 and 0 < maturity(2) < 1
    assert inferred_role("TeamsRepo") == "Repository" and inferred_role("SmtpClient") == "Integration"
    assert inferred_role("Helpers") is None


def test_render_reuse_matches_spec_shape():
    report = ReuseReport(
        proposed=ProposedAbstraction(name="InvitationTokenService"),
        verdict="reuse",
        recommendation="Evaluate it.",
        candidates=[
            ReuseCandidate(
                uid="u",
                name="TokenService",
                kind="class",
                path="src/t.ts",
                line=9,
                score=0.8,
                verdict="reuse",
                features={"name": 0.9},
                used_by=["A"],
                lifecycle="create → validate → consume → expire",
            )
        ],
    )
    text = render_reuse(report)
    assert text.splitlines()[0] == "Proposed: InvitationTokenService"
    assert "Potential existing implementation: TokenService" in text and "Existing lifecycle:" in text
    assert text.splitlines()[-1] == "Recommendation: Evaluate it."


# -- Text2Cypher guard ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cypher",
    [
        "MATCH (n) DETACH DELETE n",
        "MATCH (n) SET n.x = 1 RETURN n",
        "MERGE (n:X {a: 1}) RETURN n",
        "CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DELETE n', {})",
        "LOAD CSV FROM 'file:///x' AS row RETURN row",
        "MATCH (n) RETURN n; MATCH (m) DELETE m",
        "CALL dbms.security.listUsers()",
        "MATCH (n) CALL { WITH n REMOVE n.x } IN TRANSACTIONS RETURN n",
    ],
)
def test_static_guard_rejects_writes(cypher):
    with pytest.raises(UnsafeQuery):
        static_check(cypher)


class _SpyStore:
    def __init__(self, query_type: str = "r"):
        self.query_type = query_type
        self.executed: list[str] = []

    def explain_query_type(self, cypher: str, **params):
        return self.query_type

    def read(self, cypher: str, timeout=None, **params):
        self.executed.append(cypher)
        return [{"n": 1}]


def test_ask_refuses_before_execution():
    store = _SpyStore()
    with pytest.raises(UnsafeQuery):
        ask(store, "p", "delete everything", lambda q: "MATCH (n) DETACH DELETE n")  # type: ignore[arg-type]
    assert store.executed == []


def test_ask_refuses_when_server_says_not_read_only():
    store = _SpyStore(query_type="rw")
    with pytest.raises(UnsafeQuery, match="not read-only"):
        ask(store, "p", "q", lambda q: "MATCH (n) RETURN n")  # type: ignore[arg-type]
    assert store.executed == []


def test_ask_cleans_fences_and_adds_limit():
    store = _SpyStore()
    result = ask(store, "p", "q", lambda q: "```cypher\nMATCH (n) RETURN n;\n```")  # type: ignore[arg-type]
    assert result.cypher == "MATCH (n) RETURN n\nLIMIT 100" and store.executed == [result.cypher]
    assert clean_cypher("```\nRETURN 1\n```") == "RETURN 1"
    assert ensure_limit("MATCH (n) RETURN n LIMIT 5") == "MATCH (n) RETURN n LIMIT 5"


# -- MCP output cap -------------------------------------------------------------------------------------------------


def test_fit_trims_large_lists_and_says_so():
    big = {"summary": "s", "data": {"rows": [{"x": "y" * 200} for _ in range(500)], "small": [1]}}
    out = fit(big, limit=5_000)
    assert out["truncated"] is True and "rows" in out["next"] and len(str(out)) < 6_000
    assert fit({"summary": "ok", "data": {}}) == {"summary": "ok", "data": {}}
