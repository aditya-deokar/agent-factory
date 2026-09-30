"""The context pack (spec §15): what an agent should know before touching code, within a token budget."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import BaseModel, Field

from .token_economy import TokenEconomyReport


@lru_cache(maxsize=1)
def _encoder() -> Any:
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # offline or missing: fall back to a character estimate
        return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    return len(enc.encode(text)) if enc is not None else max(1, len(text) // 4)


class SymbolItem(BaseModel):
    uid: str
    name: str
    kind: str
    role: str | None = None
    path: str
    line: int | None = None
    score: float = 0.0
    methods: list[str] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)
    patterns: list[str] = Field(default_factory=list)
    doc: str | None = None
    why: list[str] = Field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


class ArchItem(BaseModel):
    text: str
    uids: list[str] = Field(default_factory=list)


class KnowledgeItem(BaseModel):
    uid: str
    kind: str
    title: str
    claim: str
    status: str
    confidence: float | None = None
    support: int | None = None
    violations: int | None = None
    rule_type: str | None = None
    evidence: list[str] = Field(default_factory=list)


class DepItem(BaseModel):
    uid: str
    name: str
    dependents: list[str] = Field(default_factory=list)
    tests: list[str] = Field(default_factory=list)


class FeatureItem(BaseModel):
    uid: str
    name: str
    request: str | None = None
    status: str | None = None
    outcome: str | None = None
    touched: list[str] = Field(default_factory=list)


class TestItem(BaseModel):
    path: str
    covers: list[str] = Field(default_factory=list)


class DocItem(BaseModel):
    path: str
    heading: str
    excerpt: str


class TraceItem(BaseModel):
    task: str
    outcome: str | None = None
    success: bool | None = None


class MemoryItem(BaseModel):
    preferences: list[str] = Field(default_factory=list)
    similar_tasks: list[TraceItem] = Field(default_factory=list)
    notes: str | None = None


class BudgetReport(BaseModel):
    limit: int
    used: int = 0
    per_section: dict[str, int] = Field(default_factory=dict)
    cut: list[str] = Field(default_factory=list)


class ContextPack(BaseModel):
    request: str
    project_id: str
    terms: list[str] = Field(default_factory=list)
    architecture: list[ArchItem] = Field(default_factory=list)
    reusable: list[SymbolItem] = Field(default_factory=list)
    patterns: list[KnowledgeItem] = Field(default_factory=list)
    decisions: list[KnowledgeItem] = Field(default_factory=list)
    constraints: list[KnowledgeItem] = Field(default_factory=list)
    dependencies: list[DepItem] = Field(default_factory=list)
    related_features: list[FeatureItem] = Field(default_factory=list)
    tests: list[TestItem] = Field(default_factory=list)
    docs: list[DocItem] = Field(default_factory=list)
    memory: MemoryItem = Field(default_factory=MemoryItem)
    unverified: list[KnowledgeItem] = Field(default_factory=list)
    budget: BudgetReport = Field(default_factory=lambda: BudgetReport(limit=0))
    warnings: list[str] = Field(default_factory=list)
    token_economy: TokenEconomyReport | None = None

    def relevant_symbols(self) -> list[str]:
        return [s.name for s in self.reusable]


# -- rendering --------------------------------------------------------------------------------------------------------


def _k(item: KnowledgeItem) -> str:
    meta = [item.status]
    if item.confidence is not None:
        meta.append(f"confidence {item.confidence}")
    if item.support is not None:
        meta.append(f"support {item.support}" + (f", {item.violations} violation(s)" if item.violations else ""))
    ev = f" — evidence: {', '.join(item.evidence[:3])}" if item.evidence else ""
    return f"- **{item.title}** ({'; '.join(meta)}){ev}"


def line_symbol(s: SymbolItem) -> str:
    bits = [f"- **{s.name}**" + (f" ({s.role})" if s.role else f" ({s.kind})") + f" — `{s.ref}`"]
    if s.methods:
        bits.append(f"  methods: {', '.join(s.methods[:10])}")
    if s.used_by:
        bits.append(f"  already used by: {', '.join(s.used_by[:8])}")
    if s.patterns:
        bits.append(f"  follows: {'; '.join(s.patterns[:3])}")
    if s.doc:
        bits.append(f"  {s.doc[:160]}")
    return "\n".join(bits)


def render_item(section: str, item: Any) -> str:
    if section in ("patterns", "decisions", "constraints", "unverified"):
        return _k(item)
    if section == "reusable":
        return line_symbol(item)
    if section == "architecture":
        return f"- {item.text}"
    if section == "dependencies":
        tests = f"; tests: {', '.join(item.tests[:4])}" if item.tests else ""
        return f"- **{item.name}** is used by {', '.join(item.dependents[:8]) or 'nothing else'}{tests}"
    if section == "related_features":
        return f"- **{item.name}** ({item.status}) — {item.request or ''}" + (
            f"; touched {', '.join(item.touched[:6])}" if item.touched else ""
        )
    if section == "tests":
        return f"- `{item.path}` covers {', '.join(item.covers[:6])}"
    if section == "docs":
        excerpt = " ".join(item.excerpt.replace("#", " ").split())
        return f"- `{item.path}` § {item.heading}: {excerpt[:200]}"
    return f"- {item}"


# Order and share of the budget (spec §15 order; constraints are never cut).
SECTIONS: list[tuple[str, float]] = [
    ("constraints", 0.15),
    ("reusable", 0.25),
    ("architecture", 0.15),
    ("patterns", 0.10),
    ("decisions", 0.10),
    ("related_features", 0.10),
    ("memory", 0.10),
    ("tests", 0.05),
    ("dependencies", 0.0),
    ("docs", 0.0),
    ("unverified", 0.0),
]


def apply_budget(pack: ContextPack, limit: int) -> ContextPack:
    report = BudgetReport(limit=limit)
    carry = 0
    total_used = 0
    for section, share in SECTIONS:
        if section == "memory":
            text = _memory_md(pack.memory)
            cost = count_tokens(text) if text else 0
            allowance = int(limit * share) + carry
            if cost > allowance and pack.memory.notes:
                pack.memory.notes = None
                report.cut.append("memory.notes")
                cost = count_tokens(_memory_md(pack.memory))
            report.per_section[section] = cost
            total_used += cost
            carry = max(0, allowance - cost)
            continue
        items = getattr(pack, section)
        allowance = int(limit * share) + carry
        kept, used = [], 0
        for item in items:
            cost = count_tokens(render_item(section, item))
            if section == "constraints" or used + cost <= allowance:
                kept.append(item)
                used += cost
            else:
                label = getattr(item, "name", None) or getattr(item, "title", None) or getattr(item, "path", "")
                report.cut.append(f"{section}: {label}")
        setattr(pack, section, kept)
        report.per_section[section] = used
        total_used += used
        carry = max(0, allowance - used)
    report.used = total_used
    pack.budget = report
    return pack


def _memory_md(m: MemoryItem) -> str:
    lines = []
    if m.preferences:
        lines += ["Team preferences:"] + [f"- {p}" for p in m.preferences[:6]]
    if m.similar_tasks:
        lines += ["Similar past tasks:"] + [
            f"- {t.task} → {t.outcome or 'no outcome recorded'}"
            + (" ✓" if t.success else " ✗" if t.success is False else "")
            for t in m.similar_tasks[:3]
        ]
    if m.notes:
        lines.append(m.notes[:1200])
    return "\n".join(lines)


def render_markdown(pack: ContextPack) -> str:
    out = [f"# Context for: {pack.request}", ""]
    if pack.token_economy is not None:
        out += [pack.token_economy.to_markdown_badge(), ""]

    def section(title: str, key: str, empty: str | None = None) -> None:
        items = getattr(pack, key)
        if not items and empty is None:
            return
        out.append(f"## {title}")
        out.extend(render_item(key, i) for i in items) if items else out.append(empty or "")
        out.append("")

    section("Relevant architecture", "architecture")
    section("Related features", "related_features")
    section("Reusable implementations", "reusable", "_Nothing similar found; a new abstraction may be justified._")
    section("Patterns to follow", "patterns")
    section("Decisions", "decisions")
    section("Constraints (must not be violated)", "constraints", "_No validated constraints in scope._")
    section("Dependencies (who else uses what you may change)", "dependencies")
    section("Relevant tests", "tests")
    mem = _memory_md(pack.memory)
    if mem:
        out += ["## What memory says", mem, ""]
    section("Documentation", "docs")
    section("Unverified observations (candidates — not rules)", "unverified")
    out += _plan_skeleton(pack)
    if pack.budget.cut:
        out += [
            "",
            f"_Trimmed to fit {pack.budget.limit} tokens: {', '.join(pack.budget.cut[:12])}. "
            "Ask for more with a larger budget._",
        ]
    for w in pack.warnings:
        out.append(f"_Note: {w}_")
    return "\n".join(out).rstrip() + "\n"


def _plan_skeleton(pack: ContextPack) -> list[str]:
    arch = [f"- {a.text}" for a in pack.architecture[:5]]
    reuse = [f"- {s.name} (`{s.ref}`)" for s in pack.reusable[:6]]
    pats = [f"- {p.title}" for p in pack.patterns[:6]]
    files = sorted({s.path for s in pack.reusable[:8]})
    return [
        "## Plan skeleton (fill in before editing — spec §18)",
        "",
        "```text",
        "FEATURE PLAN",
        f"Feature: {pack.request}",
        "Existing Architecture:",
        *(arch or ["- (see above)"]),
        "Existing Implementations:",
        *(reuse or ["- none found"]),
        "Reusable Patterns:",
        *(pats or ["- none validated"]),
        "Potential Files:",
        *([f"- {f}" for f in files] or ["- to decide"]),
        "New Abstractions: to decide (run find_reusable for each one)",
        "Architectural Decision: to decide",
        "Risks: to decide",
        "```",
    ]
