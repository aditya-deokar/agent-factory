"""Confidence for knowledge: Wilson lower bound on support vs violations, weighted by source.

z = 1.0 (not 1.96): real repositories have few instances per pattern, and 1.96
would keep a clean 6/6 pattern at 0.55 forever. With z = 1.0 a clean 6/6 auditor
pattern reaches 0.77, while a single counter-example (8 support, 1 violation)
holds it at 0.67 -- below the default auto-validation threshold of 0.75.
"""

from __future__ import annotations

import math

from ..schema.model import KnowledgeSource

Z = 1.0
SOURCE_WEIGHT = {
    KnowledgeSource.ADR: 1.0,
    KnowledgeSource.HUMAN: 1.0,
    KnowledgeSource.AUDITOR: 0.9,
    KnowledgeSource.AGENT: 0.75,
}
ADR_ACCEPTED_FLOOR = 0.95
HUMAN_APPROVED_FLOOR = 0.90
STALE_FACTOR = 0.9


def wilson_lower_bound(successes: int, total: int, z: float = Z) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return max(0.0, (centre - margin) / denom)


def confidence(
    support: int,
    violations: int,
    source: KnowledgeSource | str,
    *,
    stale: bool = False,
    adr_accepted: bool = False,
    human_approved: bool = False,
) -> float:
    base = wilson_lower_bound(support, support + violations)
    value = base * SOURCE_WEIGHT[KnowledgeSource(source)] * (STALE_FACTOR if stale else 1.0)
    if adr_accepted:
        value = max(value, ADR_ACCEPTED_FLOOR)
    if human_approved:
        value = max(value, HUMAN_APPROVED_FLOOR)
    return round(min(1.0, value), 2)
