"""Adapter over neo4j-agent-memory (short-term, long-term and reasoning memory, spec §8.3).

The only module that imports `neo4j_agent_memory` (its API moves between versions:
see docs/spikes/02-agent-memory.md). Every text argument is checked for secrets
first; anything with a finding is refused, not silently written.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from ..common.redact import contains_secret
from ..config import AgentFactoryConfig, ConnectionSettings


class SecretInMemory(ValueError):
    """Refused: the text contains something that looks like a credential."""


@dataclass
class TraceSummary:
    task: str
    outcome: str | None
    success: bool | None
    steps: list[str]


def _guard(*texts: str | None) -> None:
    for text in texts:
        if text and contains_secret(text):
            raise SecretInMemory("refusing to store text that looks like a secret")


def project_session(project_id: str) -> str:
    return f"project:{project_id}"


def feature_session(feature_uid: str) -> str:
    return f"feature:{feature_uid}"


class AgentMemoryPort(Protocol):
    enabled: bool

    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def add_message(self, session_id: str, role: str, content: str) -> None: ...
    async def start_trace(self, session_id: str, task: str) -> str: ...
    async def add_step(
        self,
        trace_id: str,
        thought: str,
        action: str | None = None,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        result_summary: str | None = None,
    ) -> None: ...
    async def complete_trace(self, trace_id: str, outcome: str, success: bool) -> None: ...
    async def similar_traces(self, task: str, limit: int = 3) -> list[TraceSummary]: ...
    async def save_fact(self, subject: str, predicate: str, obj: str) -> None: ...
    async def save_preference(self, category: str, preference: str) -> None: ...
    async def recall_preferences(self, topic: str, limit: int = 10) -> list[str]: ...
    async def context(self, query: str, session_id: str) -> str: ...


class NullAgentMemory:
    """Used when the memory workspace is not configured: everything works, nothing is remembered."""

    enabled = False

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        _guard(content)

    async def start_trace(self, session_id: str, task: str) -> str:
        _guard(task)
        return "null-trace"

    async def add_step(
        self,
        trace_id: str,
        thought: str,
        action: str | None = None,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        result_summary: str | None = None,
    ) -> None:
        _guard(thought, result_summary, *(str(v) for v in (arguments or {}).values()))

    async def complete_trace(self, trace_id: str, outcome: str, success: bool) -> None:
        _guard(outcome)

    async def similar_traces(self, task: str, limit: int = 3) -> list[TraceSummary]:
        return []

    async def save_fact(self, subject: str, predicate: str, obj: str) -> None:
        _guard(subject, predicate, obj)

    async def save_preference(self, category: str, preference: str) -> None:
        _guard(category, preference)

    async def recall_preferences(self, topic: str, limit: int = 10) -> list[str]:
        return []

    async def context(self, query: str, session_id: str) -> str:
        return ""


class AgentMemory:
    """neo4j-agent-memory backed implementation."""

    enabled = True

    def __init__(
        self,
        settings: ConnectionSettings,
        api_key: str | None,
        embedder: Any | None = None,
        model: str = "text-embedding-3-small",
    ):
        self._settings = settings
        self._api_key = api_key
        self._embedder = embedder
        self._model = model
        self._client: Any = None

    async def open(self) -> None:
        from neo4j_agent_memory import MemoryClient, MemorySettings
        from neo4j_agent_memory.config import EmbeddingConfig, ExtractionConfig, ExtractorType, Neo4jConfig
        from pydantic import SecretStr

        settings = MemorySettings(
            neo4j=Neo4jConfig(
                uri=self._settings.uri, username=self._settings.username, password=SecretStr(self._settings.password)
            ),
            embedding=EmbeddingConfig(api_key=SecretStr(self._api_key or "unused"), model=self._model),
            extraction=ExtractionConfig(extractor_type=ExtractorType.NONE),
        )
        self._client = MemoryClient(settings, embedder=_async_embedder(self._embedder) if self._embedder else None)
        await self._client.connect()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def _ready(self) -> Any:
        """Open on first use: callers (MCP tools, CLI) never have to manage the connection."""
        if self._client is None:
            await self.open()
        return self._client

    @property
    def client(self) -> Any:
        if self._client is None:
            raise RuntimeError("AgentMemory is not open; call await open() first")
        return self._client

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        _guard(content)
        await (await self._ready()).short_term.add_message(
            session_id, role, content, extract_entities=False, extract_relations=False
        )

    async def start_trace(self, session_id: str, task: str) -> str:
        _guard(task)
        trace = await (await self._ready()).reasoning.start_trace(session_id, task)
        return str(trace.id)

    async def add_step(
        self,
        trace_id: str,
        thought: str,
        action: str | None = None,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        result_summary: str | None = None,
    ) -> None:
        _guard(thought, result_summary, *(str(v) for v in (arguments or {}).values()))
        step = await (await self._ready()).reasoning.add_step(
            trace_id, thought=thought, action=action, observation=result_summary
        )
        if tool_name:
            await (await self._ready()).reasoning.record_tool_call(
                step.id, tool_name, arguments or {}, result=result_summary
            )

    async def complete_trace(self, trace_id: str, outcome: str, success: bool) -> None:
        _guard(outcome)
        await (await self._ready()).reasoning.complete_trace(trace_id, outcome=outcome, success=success)

    async def similar_traces(self, task: str, limit: int = 3) -> list[TraceSummary]:
        traces = await (await self._ready()).reasoning.get_similar_traces(
            task, limit=limit, success_only=False, threshold=0.3
        )
        out = []
        for t in traces:
            steps = [s.thought or s.action or "" for s in (getattr(t, "steps", None) or [])]
            out.append(TraceSummary(t.task, getattr(t, "outcome", None), getattr(t, "success", None), steps))
        return out

    async def save_fact(self, subject: str, predicate: str, obj: str) -> None:
        _guard(subject, predicate, obj)
        await (await self._ready()).long_term.add_fact(subject, predicate, obj)

    async def save_preference(self, category: str, preference: str) -> None:
        _guard(category, preference)
        await (await self._ready()).long_term.add_preference(category, preference)

    async def recall_preferences(self, topic: str, limit: int = 10) -> list[str]:
        prefs = await (await self._ready()).long_term.search_preferences(topic, limit=limit, threshold=0.3)
        return [p.preference for p in prefs]

    async def context(self, query: str, session_id: str) -> str:
        return str(await (await self._ready()).get_context(query, session_id=session_id))


def make_agent_memory(
    config: AgentFactoryConfig, env: Mapping[str, str], embedder: Any | None = None
) -> AgentMemoryPort:
    """AgentMemory on the memory workspace (or the domain DB in same-instance mode); Null if unconfigured."""
    from ..config.loader import resolve_target

    settings = resolve_target(config.neo4j.memory, env) or resolve_target(config.neo4j.domain, env)
    if settings is None:
        return NullAgentMemory()
    if embedder is None and not env.get("OPENAI_API_KEY"):
        return NullAgentMemory()
    return AgentMemory(settings, env.get("OPENAI_API_KEY"), embedder, config.embeddings.model)


def _async_embedder(embedder: Any) -> Any:
    """Wrap Agent Factory's sync Embedder in neo4j-agent-memory's async BaseEmbedder."""
    from neo4j_agent_memory.embeddings.base import BaseEmbedder

    if isinstance(embedder, BaseEmbedder):
        return embedder

    class _Adapter(BaseEmbedder):
        @property
        def dimensions(self) -> int:
            return int(embedder.dimensions)

        async def embed(self, text: str) -> list[float]:
            return list(embedder.embed([text])[0])

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [list(v) for v in embedder.embed(list(texts))]

    return _Adapter()
