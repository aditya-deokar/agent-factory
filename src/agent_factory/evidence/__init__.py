"""Evidence engine: proof collection, hashing, and PR body generation (spec §23, §24)."""

from __future__ import annotations

from .pr_body import render_pr_body
from .store import EvidenceItem, EvidenceStore

__all__ = ["EvidenceItem", "EvidenceStore", "render_pr_body"]
