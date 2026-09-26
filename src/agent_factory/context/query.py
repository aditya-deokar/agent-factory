"""Query understanding: request text -> search terms (deterministic; no LLM needed)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path

import yaml

from ..auditor.cards import split_words

STOPWORDS = {
    "a",
    "an",
    "the",
    "to",
    "for",
    "of",
    "and",
    "or",
    "in",
    "on",
    "with",
    "by",
    "from",
    "into",
    "that",
    "this",
    "add",
    "adding",
    "implement",
    "implementing",
    "build",
    "support",
    "feature",
    "new",
    "make",
    "allow",
    "let",
    "lets",
    "can",
    "should",
    "we",
    "our",
    "their",
    "them",
    "they",
    "it",
    "its",
    "be",
    "is",
    "are",
    "when",
    "so",
    "want",
    "need",
    "please",
    "how",
    "what",
    "which",
    "where",
    "does",
    "do",
    "change",
    "update",
    "fix",
    "some",
    "via",
    "use",
    "using",
    "able",
    "also",
    "all",
    "any",
    "each",
    "per",
    "who",
    "about",
}
_IDENT = re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]+)+\b|\b[a-z]+(?:[A-Z][a-z0-9]+)+\b")


def singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


@dataclass
class ParsedQuery:
    text: str
    terms: list[str]  # from the request itself, in order
    expanded: list[str] = field(default_factory=list)  # synonyms added
    identifiers: list[str] = field(default_factory=list)  # CamelCase names mentioned verbatim

    @property
    def all_terms(self) -> list[str]:
        return self.terms + [t for t in self.expanded if t not in self.terms]


@lru_cache(maxsize=8)
def _groups(extra_path: str | None) -> tuple[frozenset[str], ...]:
    text = resources.files("agent_factory.context").joinpath("synonyms.yaml").read_text(encoding="utf-8")
    groups = [frozenset(singular(w.lower()) for w in g) for g in (yaml.safe_load(text) or {}).get("groups", [])]
    if extra_path and Path(extra_path).exists():
        extra = yaml.safe_load(Path(extra_path).read_text(encoding="utf-8")) or {}
        groups += [frozenset(singular(w.lower()) for w in g) for g in extra.get("groups", [])]
    return tuple(groups)


def tokenize(text: str) -> list[str]:
    words: list[str] = []
    for w in split_words(text):
        w = singular(w)
        if len(w) > 1 and w not in STOPWORDS and not w.isdigit() and w not in words:
            words.append(w)
    return words


def understand(request: str, root: Path | None = None, expand: bool = True) -> ParsedQuery:
    terms = tokenize(request)
    identifiers = sorted(set(_IDENT.findall(request)))
    expanded: list[str] = []
    if expand:
        extra = str(root / ".agent-factory" / "synonyms.yaml") if root else None
        for group in _groups(extra):
            if group & set(terms):
                expanded += sorted(w for w in group if w not in terms and w not in expanded)
    return ParsedQuery(request, terms, expanded, identifiers)
