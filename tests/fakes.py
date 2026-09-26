"""Test doubles."""

from __future__ import annotations

from typing import Any

from agent_factory.memory.agent_memory import TraceSummary, _guard


class FakeAgentMemory:
    """Records every call; returns scripted traces and preferences. Enforces the same secret guard."""

    enabled = True

    def __init__(self, traces: list[TraceSummary] | None = None, preferences: list[str] | None = None):
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.traces = traces or []
        self.preferences = preferences or []
        self._n = 0

    async def open(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def add_message(self, session_id: str, role: str, content: str) -> None:
        _guard(content)
        self.calls.append(("add_message", {"session_id": session_id, "role": role, "content": content}))

    async def start_trace(self, session_id: str, task: str) -> str:
        _guard(task)
        self._n += 1
        self.calls.append(("start_trace", {"session_id": session_id, "task": task}))
        return f"trace-{self._n}"

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
        self.calls.append(
            (
                "add_step",
                {
                    "trace_id": trace_id,
                    "thought": thought,
                    "action": action,
                    "tool_name": tool_name,
                    "arguments": arguments or {},
                },
            )
        )

    async def complete_trace(self, trace_id: str, outcome: str, success: bool) -> None:
        self.calls.append(("complete_trace", {"trace_id": trace_id, "outcome": outcome, "success": success}))

    async def similar_traces(self, task: str, limit: int = 3) -> list[TraceSummary]:
        return self.traces[:limit]

    async def save_fact(self, subject: str, predicate: str, obj: str) -> None:
        self.calls.append(("save_fact", {"s": subject, "p": predicate, "o": obj}))

    async def save_preference(self, category: str, preference: str) -> None:
        self.calls.append(("save_preference", {"category": category, "preference": preference}))

    async def recall_preferences(self, topic: str, limit: int = 10) -> list[str]:
        return self.preferences[:limit]

    async def context(self, query: str, session_id: str) -> str:
        return ""

    def steps(self) -> list[dict[str, Any]]:
        return [c for name, c in self.calls if name == "add_step"]
