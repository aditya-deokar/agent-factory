"""The 8 Anti-Slop Guardrail checks (spec §22)."""

from __future__ import annotations

from .abstraction import check_abstraction
from .architecture import check_architecture
from .complexity import check_complexity
from .consistency import check_consistency
from .duplication import check_duplication
from .regression import check_regression
from .reusability import check_reusability
from .scope import check_scope

__all__ = [
    "check_abstraction",
    "check_architecture",
    "check_complexity",
    "check_consistency",
    "check_duplication",
    "check_regression",
    "check_reusability",
    "check_scope",
]
