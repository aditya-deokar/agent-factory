"""The knowledge lifecycle (spec §20): who may move knowledge between which states.

    candidate --approve/auto--> validated --deprecate--> deprecated
        |                          |   ^                     |
        | reject          supersede|   +---- re-observed ----+
        v                          v
    rejected (terminal)       superseded (terminal)

Actors are `auditor`, `adr`, `agent:<name>`, `human:<name>`, `revalidation`.
"""

from __future__ import annotations

from ..schema.model import KnowledgeStatus as S


class IllegalTransition(ValueError):
    pass


ACTOR_KINDS = ("auditor", "adr", "agent", "human", "revalidation")

# (from, to) -> actor kinds allowed. Same-status "transitions" are no-ops and always allowed.
TRANSITIONS: dict[tuple[S, S], frozenset[str]] = {
    (S.CANDIDATE, S.VALIDATED): frozenset({"auditor", "adr", "human", "agent", "revalidation"}),
    (S.CANDIDATE, S.REJECTED): frozenset({"human", "agent", "adr"}),
    (S.CANDIDATE, S.SUPERSEDED): frozenset({"human", "adr"}),
    (S.CANDIDATE, S.DEPRECATED): frozenset({"human", "adr"}),
    (S.VALIDATED, S.DEPRECATED): frozenset({"revalidation", "auditor", "human", "adr"}),
    (S.VALIDATED, S.SUPERSEDED): frozenset({"adr", "human"}),
    (S.DEPRECATED, S.VALIDATED): frozenset({"revalidation", "auditor", "human", "adr"}),
    (S.DEPRECATED, S.SUPERSEDED): frozenset({"adr", "human"}),
}

# Agents may only validate when re-derivation from the code proves the claim (checked by the caller).
AGENT_NEEDS_REDERIVATION = {(S.CANDIDATE, S.VALIDATED)}


def actor_kind(actor: str) -> str:
    kind = actor.split(":", 1)[0]
    if kind not in ACTOR_KINDS:
        raise ValueError(f"unknown actor {actor!r}; expected one of {ACTOR_KINDS} (optionally ':name')")
    return kind


def check_transition(current: S | str, target: S | str, actor: str, *, rederived: bool = False) -> None:
    cur, tgt = S(current), S(target)
    if cur == tgt:
        return
    kind = actor_kind(actor)
    allowed = TRANSITIONS.get((cur, tgt))
    if allowed is None:
        raise IllegalTransition(f"{cur.value} -> {tgt.value} is not a valid knowledge transition")
    if kind not in allowed:
        raise IllegalTransition(f"{kind} may not move knowledge from {cur.value} to {tgt.value}")
    if kind == "agent" and (cur, tgt) in AGENT_NEEDS_REDERIVATION and not rederived:
        raise IllegalTransition(
            "agents cannot validate knowledge on their own; it must re-derive from the code or be approved by a human"
        )


def is_terminal(status: S | str) -> bool:
    return S(status) in (S.REJECTED, S.SUPERSEDED)
