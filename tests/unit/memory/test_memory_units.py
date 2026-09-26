"""Phase 4 pure logic: confidence, lifecycle, validation, status policy, agent-memory guard, embeddings."""

from __future__ import annotations

import asyncio
import itertools
from pathlib import Path

import pytest

from agent_factory.common.embed import CachedEmbedder, HashEmbedder, cosine
from agent_factory.config.model import ValidationConfig
from agent_factory.memory.agent_memory import AgentMemory, NullAgentMemory, SecretInMemory
from agent_factory.memory.confidence import confidence, wilson_lower_bound
from agent_factory.memory.lifecycle import TRANSITIONS, IllegalTransition, check_transition, is_terminal
from agent_factory.memory.validation import (
    EvidenceRef,
    FileCache,
    Proposal,
    check_evidence,
    decide_status,
    score,
    secret_findings,
)
from agent_factory.schema.model import KnowledgeStatus as S

# -- confidence -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "support,violations,source,expected",
    [
        (6, 0, "auditor", 0.77),
        (10, 0, "auditor", 0.82),
        (30, 0, "auditor", 0.87),
        (8, 1, "auditor", 0.67),
        (3, 0, "agent", 0.56),
        (0, 0, "auditor", 0.0),
        (0, 5, "human", 0.0),
    ],
)
def test_confidence_values(support, violations, source, expected):
    assert confidence(support, violations, source) == expected


def test_confidence_floors_and_stale():
    assert confidence(1, 0, "adr", adr_accepted=True) == 0.95
    assert confidence(1, 3, "human", human_approved=True) == 0.90
    assert confidence(10, 0, "auditor", stale=True) == round(0.9 * 0.9 * wilson_lower_bound(10, 10), 2)


def test_wilson_is_monotonic_in_evidence():
    values = [wilson_lower_bound(n, n) for n in range(1, 30)]
    assert values == sorted(values) and values[0] == 0.5


# -- lifecycle ------------------------------------------------------------------------------------------------------

ACTORS = ["auditor", "adr", "agent:x", "human:y", "revalidation"]


@pytest.mark.parametrize("current,target,actor", list(itertools.product(list(S), list(S), ACTORS)))
def test_transition_matrix(current, target, actor):
    kind = actor.split(":")[0]
    allowed = current == target or (
        (current, target) in TRANSITIONS
        and kind in TRANSITIONS[(current, target)]
        and not (kind == "agent" and (current, target) == (S.CANDIDATE, S.VALIDATED))
    )
    if allowed:
        check_transition(current, target, actor)
    else:
        with pytest.raises(IllegalTransition):
            check_transition(current, target, actor)


def test_agent_may_validate_only_when_rederived():
    with pytest.raises(IllegalTransition, match="re-derive"):
        check_transition(S.CANDIDATE, S.VALIDATED, "agent:claude")
    check_transition(S.CANDIDATE, S.VALIDATED, "agent:claude", rederived=True)


def test_terminal_states_and_unknown_actor():
    assert is_terminal(S.REJECTED) and is_terminal(S.SUPERSEDED) and not is_terminal(S.DEPRECATED)
    with pytest.raises(ValueError):
        check_transition(S.CANDIDATE, S.VALIDATED, "robot")


# -- validation -----------------------------------------------------------------------------------------------------


def test_evidence_ref_parse():
    assert EvidenceRef.parse("src/a.ts:12-40").model_dump(include={"path", "line_start", "line_end"}) == {
        "path": "src/a.ts",
        "line_start": 12,
        "line_end": 40,
    }
    assert EvidenceRef.parse("src\\a.ts:7").line_end == 7
    with pytest.raises(ValueError):
        EvidenceRef.parse("../outside.ts")


def test_check_evidence_drops_missing_and_out_of_range(tmp_path: Path):
    (tmp_path / "a.ts").write_text("1\n2\n3\n", encoding="utf-8")
    refs = [EvidenceRef.parse("a.ts:1-2"), EvidenceRef.parse("a.ts:2-99"), EvidenceRef.parse("missing.ts")]
    kept, dropped = check_evidence(refs, FileCache(tmp_path))
    assert [r.line_end for r in kept] == [2] and kept[0].content_hash
    assert len(dropped) == 2 and "outside the file" in dropped[0] and "not found" in dropped[1]


def _p(**kw) -> Proposal:
    base = {
        "kind": "pattern",
        "title": "Services own logic",
        "claim": "Business logic lives in services.",
        "source": "auditor",
        "evidence": [EvidenceRef(path="a.ts")],
    }
    return Proposal.model_validate({**base, **kw})


def test_secret_findings_cover_notes():
    assert secret_findings(_p(claim="use AKIAIOSFODNN7EXAMPLE")) == ["claim"]
    assert secret_findings(_p(evidence=[EvidenceRef(path="a.ts", note="password: 'hunter2secret'")])) == [
        "evidence[0].note"
    ]
    assert secret_findings(_p()) == []


CFG = ValidationConfig()


@pytest.mark.parametrize(
    "existing,kw,expected",
    [
        (None, {"support_count": 6, "violation_count": 0}, S.VALIDATED),
        (None, {"support_count": 2, "violation_count": 0}, S.CANDIDATE),
        (None, {"source": "agent", "support_count": 10}, S.CANDIDATE),  # agents never auto-validate
        (None, {"source": "agent", "support_count": 10, "rederived": True}, S.VALIDATED),
        ({"status": "candidate"}, {"support_count": 8, "violation_count": 1}, S.CANDIDATE),
        ({"status": "validated", "support_count": 6}, {"support_count": 2}, S.DEPRECATED),
        ({"status": "validated", "validated_by": "human:a"}, {"support_count": 4}, S.VALIDATED),
        ({"status": "validated", "validated_by": "human:a"}, {"support_count": 1}, S.DEPRECATED),
        ({"status": "deprecated"}, {"support_count": 9}, S.VALIDATED),
        ({"status": "rejected"}, {"support_count": 30}, S.REJECTED),
        (None, {"source": "adr", "adr_status": "superseded", "kind": "decision"}, S.SUPERSEDED),
    ],
)
def test_status_policy(existing, kw, expected):
    p = _p(**kw)
    support, _, conf = score(p, human_approved=bool(existing and existing.get("validated_by")))
    assert decide_status(existing, p, support, conf, CFG).status == expected


# -- agent memory guard ---------------------------------------------------------------------------------------------


def test_null_agent_memory_refuses_secrets_and_otherwise_noops():
    mem = NullAgentMemory()
    asyncio.run(mem.add_message("s", "user", "Add team invitations"))
    with pytest.raises(SecretInMemory):
        asyncio.run(mem.save_fact("db", "password", "postgres://u:hunter22pw@db/x"))
    assert asyncio.run(mem.similar_traces("x")) == []


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        async def call(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return call


def test_agent_memory_never_sends_secrets_to_the_client():
    mem = AgentMemory.__new__(AgentMemory)
    rec = _Recorder()
    mem._client = type("C", (), {"long_term": rec, "short_term": rec, "reasoning": rec})()
    asyncio.run(mem.save_fact("TeamInvitation", "uses", "TokenService"))
    with pytest.raises(SecretInMemory):
        asyncio.run(mem.add_message("s", "user", "my key is sk-proj-abcdefghijklmnopqrstuvwx1234"))
    assert [c[0] for c in rec.calls] == ["add_fact"]


# -- embeddings -----------------------------------------------------------------------------------------------------


def test_hash_embedder_is_deterministic_and_lexical():
    e = HashEmbedder(256)
    a, b, c = e.embed(["TokenService create validate", "token service create", "EmailService send template"])
    assert e.embed(["TokenService create validate"])[0] == a
    assert cosine(a, b) > cosine(a, c)


def test_cached_embedder_hits(tmp_path: Path):
    class Counting(HashEmbedder):
        calls = 0

        def embed(self, texts):
            Counting.calls += len(texts)
            return super().embed(texts)

    cache = CachedEmbedder(Counting(64), tmp_path / "e.sqlite")
    first = cache.embed(["a", "b"])
    second = cache.embed(["a", "b", "c"])
    assert Counting.calls == 3 and cache.hits == 2 and first == second[:2]
    assert max(abs(x - y) for x, y in zip(first[0], second[0], strict=True)) < 1e-6
    cache.close()
