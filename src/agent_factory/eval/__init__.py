"""Evaluation: retrieval quality, reuse detection quality, and the retrieval ablation (Phase 5 gates)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..context.engine import ALL_MODES, ContextEngine
from ..context.reuse import ProposedAbstraction, ReuseDetector

K = 8
ABLATION_MODES: dict[str, frozenset[str]] = {
    "vector only": frozenset({"vector"}),
    "full-text only": frozenset({"fulltext"}),
    "all retrievers (no graph)": ALL_MODES - {"graph"},
    "all retrievers + graph expansion": ALL_MODES,
}


@dataclass
class RetrievalScore:
    mode: str
    recall_at_k: float
    mrr: float
    per_case: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReuseScore:
    precision: float
    recall: float
    accuracy: float
    per_case: list[dict[str, Any]] = field(default_factory=list)


def load_cases(path: Path) -> list[dict[str, Any]]:
    return list((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("cases", []))


def _name(uid: str) -> str:
    return uid.rsplit("#", 1)[-1].split(".")[0]


def score_retrieval(
    engine: ContextEngine, cases: list[dict[str, Any]], mode: str, modes: frozenset[str] = ALL_MODES, k: int = K
) -> RetrievalScore:
    recalls, rrs, rows = [], [], []
    for case in cases:
        ranked = [_name(u) for u in engine.retrieve(case["request"], modes).ranked()]
        top = ranked[:k]
        expected = case["expect"]
        hit = [e for e in expected if e in top]
        recalls.append(len(hit) / len(expected))
        first = next((i for i, n in enumerate(ranked, start=1) if n in expected), None)
        rrs.append(1 / first if first else 0.0)
        rows.append(
            {
                "request": case["request"],
                "split": case.get("split"),
                "recall": round(recalls[-1], 2),
                "missing": [e for e in expected if e not in top],
                "top": top,
            }
        )
    n = max(1, len(cases))
    return RetrievalScore(mode, round(sum(recalls) / n, 3), round(sum(rrs) / n, 3), rows)


def score_reuse(detector: ReuseDetector, cases: list[dict[str, Any]]) -> ReuseScore:
    tp = fp = fn = tn = 0
    rows = []
    for case in cases:
        report = detector.find(
            ProposedAbstraction(name=case["name"], description=case.get("desc", ""), methods=case.get("methods", []))
        )
        top = report.top
        flagged = top is not None and top.verdict in ("reuse", "extend")
        expected = case.get("expect") or []
        if expected:
            correct = flagged and top is not None and top.name in expected
            tp += correct
            fn += not correct
        else:
            fp += flagged
            tn += not flagged
            correct = not flagged
        rows.append(
            {
                "name": case["name"],
                "expected": expected,
                "top": top.name if top else None,
                "score": top.score if top else None,
                "verdict": report.verdict,
                "correct": correct,
            }
        )
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return ReuseScore(round(precision, 3), round(recall, 3), round((tp + tn) / max(1, len(cases)), 3), rows)


def run_ablation(
    engine: ContextEngine, cases: list[dict[str, Any]]
) -> tuple[list[RetrievalScore], dict[str, list[RetrievalScore]]]:
    overall = [score_retrieval(engine, cases, name, modes) for name, modes in ABLATION_MODES.items()]
    splits: dict[str, list[RetrievalScore]] = {}
    for split in sorted({str(c["split"]) for c in cases if c.get("split")}):
        sub = [c for c in cases if c.get("split") == split]
        splits[split] = [score_retrieval(engine, sub, name, modes) for name, modes in ABLATION_MODES.items()]
    return overall, splits


def ablation_markdown(
    results: list[RetrievalScore],
    reuse: ReuseScore | None,
    embedder_model: str | None,
    splits: dict[str, list[RetrievalScore]] | None = None,
) -> str:
    lines = [
        "# Retrieval ablation",
        "",
        f"Fixture: `teamapp-ts` · {len(results[0].per_case) if results else 0} hand-written requests · "
        f"recall@{K} and MRR.",
        f"Embeddings: `{embedder_model or 'none'}`"
        + (
            " (offline lexical stand-in; re-run with real embeddings for semantic numbers)"
            if embedder_model and embedder_model.startswith("hash")
            else ""
        ),
        "",
        f"| Retrieval | recall@{K} | MRR |",
        "|---|---|---|",
    ]
    lines += [f"| {r.mode} | {r.recall_at_k:.3f} | {r.mrr:.3f} |" for r in results]
    for split, scores in (splits or {}).items():
        n = len(scores[0].per_case) if scores else 0
        lines += [
            "",
            f"**{split} split** ({n} requests"
            + ("; weights were tuned on these)" if split == "tune" else "; never used for tuning)"),
            "",
            f"| Retrieval | recall@{K} | MRR |",
            "|---|---|---|",
        ]
        lines += [f"| {r.mode} | {r.recall_at_k:.3f} | {r.mrr:.3f} |" for r in scores]
    if results:
        best = results[-1]
        lines += ["", f"Misses with {best.mode}:", ""]
        misses = [c for c in best.per_case if c["missing"]]
        lines += [f"- {c['request']}: missing {', '.join(c['missing'])}" for c in misses] or ["- none"]
    if reuse is not None:
        lines += [
            "",
            "## Reuse detection",
            "",
            f"precision {reuse.precision:.3f} · recall {reuse.recall:.3f} · accuracy {reuse.accuracy:.3f}",
            "",
            "| Proposed | Expected | Top candidate | Score | Verdict | OK |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {c['name']} | {', '.join(c['expected']) or '(new)'} | {c['top'] or '-'} | {c['score']} | "
            f"{c['verdict']} | {'✓' if c['correct'] else '✗'} |"
            for c in reuse.per_case
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - manual tool
    """python -m agent_factory.eval <repo> [--out docs/eval/retrieval-ablation.md]"""
    import argparse

    from ..runtime import Runtime

    parser = argparse.ArgumentParser()
    parser.add_argument("repo", type=Path)
    parser.add_argument("--cases", type=Path, default=Path("tests/eval"))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    rt = Runtime.open(args.repo.resolve())
    try:
        engine = rt.engine()
        results, splits = run_ablation(engine, load_cases(args.cases / "retrieval_cases.yaml"))
        reuse = score_reuse(rt.reuse(), load_cases(args.cases / "reuse_cases.yaml"))
        text = ablation_markdown(results, reuse, rt.embedder.model if rt.embedder else None, splits)
    finally:
        rt.close()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)
