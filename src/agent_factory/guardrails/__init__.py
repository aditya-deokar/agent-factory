"""Anti-slop guardrails engine and diff analyzer (spec §22)."""

from __future__ import annotations

from .diff import DiffFile, DiffFragment, analyze_diff
from .model import CheckResult, CheckSeverity, Finding, GuardrailReport
from .rules import ALL_CHECKS, run_guardrails

__all__ = [
    "ALL_CHECKS",
    "CheckResult",
    "CheckSeverity",
    "DiffFile",
    "DiffFragment",
    "Finding",
    "GuardrailReport",
    "analyze_diff",
    "run_guardrails",
]
