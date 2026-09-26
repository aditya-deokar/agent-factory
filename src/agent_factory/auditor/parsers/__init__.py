"""Language adapters: source text -> ParsedFile."""

from __future__ import annotations

from typing import Protocol

from ..model import ParsedFile, SourceFile
from .python import PythonAdapter
from .typescript import TypeScriptAdapter


class LanguageAdapter(Protocol):
    langs: frozenset[str]

    def parse(self, source: SourceFile, text: str) -> ParsedFile: ...


_ADAPTERS: list[LanguageAdapter] = [TypeScriptAdapter(), PythonAdapter()]


def adapter_for(lang: str) -> LanguageAdapter | None:
    return next((a for a in _ADAPTERS if lang in a.langs), None)


def parse_source(source: SourceFile, text: str) -> ParsedFile | None:
    adapter = adapter_for(source.lang)
    return adapter.parse(source, text) if adapter is not None else None


__all__ = ["LanguageAdapter", "PythonAdapter", "TypeScriptAdapter", "adapter_for", "parse_source"]
