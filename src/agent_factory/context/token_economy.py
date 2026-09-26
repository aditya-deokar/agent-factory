"""Token economy and surgical retrieval calculator (spec §15, §28).

Quantifies the "Exploratory Token Tax" eliminated by graph-guided surgical context retrieval:
instead of blind directory traversal and broad file scans (75,000+ tokens polluting context),
Neo4j traversals pinpoint the exact relevant symbols (~1,000 - 4,000 tokens), preserving
pristine reasoning focus and eliminating attention degradation.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class TokenEconomyReport:
    total_repo_files: int
    targeted_files: int
    files_avoided: int
    surgical_retrieval_ratio: float  # e.g., 94.2%
    blind_exploration_tokens_est: int  # e.g., 62,500 tokens
    graph_context_tokens: int  # e.g., 3,420 tokens
    tokens_saved: int  # e.g., 59,080 tokens
    savings_percentage: float  # e.g., 94.5%
    estimated_cost_saved_usd: float  # e.g., $0.18
    context_health: str  # "Pristine" | "Focused" | "Moderate" | "Diluted"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_markdown_badge(self) -> str:
        return (
            f"> ⚡ **Graph-Guided Surgical Retrieval:** **{self.savings_percentage:.1f}% token savings** "
            f"(avoided {self.files_avoided} files) | "
            f"**Context Window: {self.context_health}** ({self.graph_context_tokens:,} tokens)"
        )

    def to_markdown_table(self) -> str:
        cost_blind = round((self.blind_exploration_tokens_est / 1_000_000) * 3.00, 2)
        cost_graph = round((self.graph_context_tokens / 1_000_000) * 3.00, 2)
        multiplier = (
            round(self.blind_exploration_tokens_est / max(1, self.graph_context_tokens), 1)
            if self.graph_context_tokens > 0
            else 1.0
        )
        scanned_blind = min(self.total_repo_files, 25)

        lines = [
            "| Metric | Blind Exploration (Baseline) | Agent Factory (Graph-Guided) | Advantage |",
            "|---|---|---|---|",
            f"| **Files Targeted / Read** | {scanned_blind} files | {self.targeted_files} files | {self.surgical_retrieval_ratio:.1f}% fewer files |",
            f"| **Tokens Consumed** | {self.blind_exploration_tokens_est:,} tokens | {self.graph_context_tokens:,} tokens | {self.savings_percentage:.1f}% token savings |",
            f"| **Context Window Health** | Polluted (diluted reasoning) | {self.context_health} (high focus) | Maximum reasoning focus |",
            f"| **Estimated Run Cost** | ${cost_blind:.2f} | ${cost_graph:.2f} | {multiplier}x cheaper |",
        ]
        return "\n".join(lines)


def compute_token_economy(
    total_repo_files: int,
    targeted_files: int,
    graph_pack_tokens: int,
) -> TokenEconomyReport:
    """Compute token economy and surgical context efficiency metrics."""
    total = max(0, total_repo_files)
    targeted = max(0, targeted_files)

    if total == 0:
        total = max(25, targeted * 5)
    if targeted > total:
        total = targeted

    files_avoided = max(0, total - targeted)
    retrieval_ratio = round((files_avoided / max(1, total)) * 100, 1)

    blind_exploration_tokens_est = min(total, 25) * 2500
    tokens_saved = max(0, blind_exploration_tokens_est - graph_pack_tokens)
    savings_pct = (
        round((tokens_saved / max(1, blind_exploration_tokens_est)) * 100, 1)
        if blind_exploration_tokens_est > 0
        else 0.0
    )
    cost_saved = round((tokens_saved / 1_000_000) * 3.00, 3)

    if graph_pack_tokens < 5000:
        health = "Pristine"
    elif graph_pack_tokens < 10000:
        health = "Focused"
    elif graph_pack_tokens < 20000:
        health = "Moderate"
    else:
        health = "Diluted"

    return TokenEconomyReport(
        total_repo_files=total,
        targeted_files=targeted,
        files_avoided=files_avoided,
        surgical_retrieval_ratio=retrieval_ratio,
        blind_exploration_tokens_est=blind_exploration_tokens_est,
        graph_context_tokens=graph_pack_tokens,
        tokens_saved=tokens_saved,
        savings_percentage=savings_pct,
        estimated_cost_saved_usd=cost_saved,
        context_health=health,
    )
