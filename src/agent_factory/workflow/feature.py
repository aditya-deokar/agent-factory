"""Feature sessions: short-term task memory for one feature (spec §10, §17, §18).

A session ties together the Feature node in the domain graph, a plan file in
.agent-factory/features/<id>/, and a reasoning trace in agent memory, so every
MCP tool call made while building the feature is remembered with it.
Phase 8 adds guardrails, evidence and the memory commit on top of this.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ..common.paths import git
from ..common.redact import redact
from ..db.repos import FeatureRepo
from ..memory.agent_memory import feature_session
from ..schema.model import FeatureStatus, Rel
from ..schema.uids import feature_id as make_feature_id
from ..schema.uids import feature_uid

if TYPE_CHECKING:
    from ..runtime import Runtime


class ReuseDecision(BaseModel):
    proposed: str
    verdict: str = Field(description="reuse | extend | new_ok (from find_reusable)")
    chosen: str | None = Field(default=None, description="The existing symbol reused or extended, if any")
    justification: str | None = None


class NewAbstraction(BaseModel):
    name: str
    justification: str


class FeaturePlan(BaseModel):
    """The §18 plan, structured."""

    summary: str = ""
    existing_architecture: list[str] = Field(default_factory=list)
    reuse_decisions: list[ReuseDecision] = Field(default_factory=list)
    planned_files: list[str] = Field(default_factory=list)
    planned_modules: list[str] = Field(default_factory=list)
    new_abstractions: list[NewAbstraction] = Field(default_factory=list)
    new_dependencies: list[str] = Field(default_factory=list)
    cited_knowledge: list[str] = Field(
        default_factory=list, description="uids of patterns/decisions/constraints followed"
    )
    architectural_decision: str | None = None
    risks: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class FeatureState(BaseModel):
    feature_id: str
    uid: str
    name: str
    request: str
    branch: str | None = None
    base_sha: str | None = None
    status: str = FeatureStatus.PLANNING.value
    trace_id: str | None = None
    created_at: str
    updated_at: str | None = None
    plan: FeaturePlan | None = None
    steps: int = 0


class FeatureNotFound(LookupError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class FeatureService:
    def __init__(self, rt: Runtime):
        self.rt = rt
        self.repo = FeatureRepo(rt.store, rt.project_id)
        self.dir = rt.root / ".agent-factory" / "features"

    # -- local state ---------------------------------------------------------------------------------------------------

    def _path(self, fid: str) -> Path:
        return self.dir / fid / "state.json"

    def _save(self, state: FeatureState) -> None:
        path = self._path(state.feature_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        index = self._index()
        if state.branch:
            index[state.branch] = state.feature_id
        (self.dir / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")

    def _index(self) -> dict[str, str]:
        path = self.dir / "index.json"
        try:
            return dict(json.loads(path.read_text(encoding="utf-8"))) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def get(self, fid: str) -> FeatureState:
        path = self._path(fid)
        if not path.exists():
            raise FeatureNotFound(f"no feature session {fid!r}; start one with start_feature")
        return FeatureState.model_validate_json(path.read_text(encoding="utf-8"))

    def list(self) -> list[FeatureState]:
        if not self.dir.exists():
            return []
        states = [
            FeatureState.model_validate_json(p.read_text(encoding="utf-8")) for p in self.dir.glob("*/state.json")
        ]
        return sorted(states, key=lambda s: s.created_at, reverse=True)

    def current_branch(self) -> str | None:
        return git(self.rt.root, "branch", "--show-current", check=False).strip() or None

    def active(self, fid: str | None = None) -> FeatureState | None:
        """The feature a tool call belongs to: explicit id, else the one mapped to the current branch."""
        if fid:
            return self.get(fid)
        branch = self.current_branch()
        mapped = self._index().get(branch or "")
        if mapped:
            state = self.get(mapped)
            if state.status not in (FeatureStatus.DONE.value, FeatureStatus.ABANDONED.value):
                return state
        return None

    # -- lifecycle -----------------------------------------------------------------------------------------------------

    async def start(self, name: str, request: str, branch: str | None = None) -> FeatureState:
        name = redact(name).text[:120]
        request = redact(request).text[:2000]
        fid = make_feature_id(name)
        n = 2
        while self._path(fid).exists():
            fid = f"{make_feature_id(name)}-{n}"
            n += 1
        branch = branch or self.current_branch()
        base = (
            git(self.rt.root, "merge-base", "HEAD", "origin/main", check=False).strip()
            or git(self.rt.root, "rev-parse", "HEAD", check=False).strip()
            or None
        )
        state = FeatureState(
            feature_id=fid,
            uid=feature_uid(self.rt.project_id, fid),
            name=name,
            request=request,
            branch=branch,
            base_sha=base,
            created_at=_now(),
        )
        self.repo.create(state.uid, name, request, feature_id=fid, branch=branch, base_sha=base)
        memory = self.rt.memory
        try:
            await memory.add_message(feature_session(state.uid), "user", request)
            state.trace_id = await memory.start_trace(feature_session(state.uid), request)
        except Exception as error:  # memory is additive; the session still works without it
            state.trace_id = None
            self.rt.warnings.append(f"agent memory unavailable: {type(error).__name__}")
        self._save(state)
        return state

    async def record_plan(self, fid: str | None, plan: FeaturePlan) -> FeatureState:
        state = self.active(fid)
        if state is None:
            raise FeatureNotFound("no active feature on this branch; call start_feature first")
        state.plan = plan
        state.status = FeatureStatus.IN_PROGRESS.value
        state.updated_at = _now()
        self.repo.update(
            state.uid,
            status=state.status,
            plan_summary=redact(plan.summary).text[:1000],
            planned_files=plan.planned_files,
            new_abstractions=[a.name for a in plan.new_abstractions],
        )
        cited = [u for u in plan.cited_knowledge if u.startswith(f"{self.rt.project_id}:k:")]
        if cited:
            self.repo.link(state.uid, Rel.FOLLOWED, cited)
        path = self._path(state.feature_id).parent / "plan.md"
        path.write_text(render_plan(state), encoding="utf-8")
        self._save(state)
        await self.record_step(
            state.feature_id,
            "Recorded the implementation plan",
            action="record_plan",
            result_summary=plan.summary[:300],
        )
        return self.get(state.feature_id)

    async def record_step(
        self,
        fid: str | None,
        thought: str,
        action: str | None = None,
        tool_name: str | None = None,
        arguments: dict[str, Any] | None = None,
        result_summary: str | None = None,
    ) -> FeatureState | None:
        state = self.active(fid)
        if state is None:
            return None
        safe_args = {k: redact(str(v)).text[:500] for k, v in (arguments or {}).items()}
        if state.trace_id:
            try:
                await self.rt.memory.add_step(
                    state.trace_id,
                    redact(thought).text[:1000],
                    action,
                    tool_name,
                    safe_args,
                    redact(result_summary or "").text[:1000] or None,
                )
            except Exception as error:
                self.rt.warnings.append(f"could not record step: {type(error).__name__}")
        state.steps += 1
        state.updated_at = _now()
        self._save(state)
        return state


def render_plan(state: FeatureState) -> str:
    p = state.plan or FeaturePlan()
    out = ["# FEATURE PLAN", "", f"Feature: {state.name}", f"Request: {state.request}", ""]
    if p.summary:
        out += [p.summary, ""]

    def block(title: str, items: list[str]) -> None:
        out.extend([f"## {title}", *([f"- {i}" for i in items] or ["- none"]), ""])

    block("Existing Architecture", p.existing_architecture)
    block(
        "Reuse decisions",
        [
            f"{d.proposed}: {d.verdict}"
            + (f" → {d.chosen}" if d.chosen else "")
            + (f" ({d.justification})" if d.justification else "")
            for d in p.reuse_decisions
        ],
    )
    block("Potential Files", p.planned_files)
    block("New Abstractions", [f"{a.name}: {a.justification}" for a in p.new_abstractions])
    block("New Dependencies", p.new_dependencies)
    block("Knowledge followed", p.cited_knowledge)
    out += ["## Architectural Decision", p.architectural_decision or "none", ""]
    block("Risks", p.risks)
    block("Assumptions", p.assumptions)
    block("Open questions", p.open_questions)
    return "\n".join(out)
