"""Evidence collectors."""

from __future__ import annotations

from .diff import collect_diff_evidence
from .runner import collect_command_evidence

__all__ = ["collect_command_evidence", "collect_diff_evidence"]
