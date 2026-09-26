"""Unit tests for the token economy and surgical retrieval engine."""

from __future__ import annotations

from agent_factory.context.token_economy import TokenEconomyReport, compute_token_economy


def test_compute_token_economy_standard_repo():
    report = compute_token_economy(total_repo_files=60, targeted_files=3, graph_pack_tokens=3500)
    assert report.total_repo_files == 60
    assert report.targeted_files == 3
    assert report.files_avoided == 57
    assert report.surgical_retrieval_ratio == 95.0
    assert report.blind_exploration_tokens_est == 62_500  # min(60, 25) * 2500
    assert report.graph_context_tokens == 3500
    assert report.tokens_saved == 59_000
    assert report.savings_percentage == 94.4
    assert report.estimated_cost_saved_usd == 0.177
    assert report.context_health == "Pristine"

    badge = report.to_markdown_badge()
    assert "94.4% token savings" in badge
    assert "Context Window: Pristine" in badge
    assert "avoided 57 files" in badge

    table = report.to_markdown_table()
    assert "Blind Exploration (Baseline)" in table
    assert "Agent Factory (Graph-Guided)" in table
    assert "59,000 tokens" in table or "3,500 tokens" in table

    data = report.to_dict()
    assert data["files_avoided"] == 57
    assert data["savings_percentage"] == 94.4


def test_compute_token_economy_edge_cases():
    # Zero repo files falls back to baseline
    rep0 = compute_token_economy(total_repo_files=0, targeted_files=0, graph_pack_tokens=1000)
    assert rep0.total_repo_files >= 25
    assert rep0.tokens_saved > 0
    assert rep0.context_health == "Pristine"

    # Targeted files exceeds total
    rep_overflow = compute_token_economy(total_repo_files=5, targeted_files=10, graph_pack_tokens=15_000)
    assert rep_overflow.total_repo_files == 10
    assert rep_overflow.files_avoided == 0
    assert rep_overflow.context_health == "Moderate"


def test_context_health_bands():
    assert compute_token_economy(30, 2, 4999).context_health == "Pristine"
    assert compute_token_economy(30, 2, 5000).context_health == "Focused"
    assert compute_token_economy(30, 2, 9999).context_health == "Focused"
    assert compute_token_economy(30, 2, 10000).context_health == "Moderate"
    assert compute_token_economy(30, 2, 19999).context_health == "Moderate"
    assert compute_token_economy(30, 2, 20000).context_health == "Diluted"
