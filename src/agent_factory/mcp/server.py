"""The Agent Factory MCP server (spec §28): the engine, exposed to any MCP-capable coding agent.

Design rules (plan phase-06):
- Thin: each tool calls the same service as its CLI twin.
- Read tools only read (READ transactions). Write tools can only create candidates, feature sessions,
  trace steps and evidence. Nothing an agent does validates knowledge on its own.
- Every tool call during an active feature is recorded as a reasoning step (arguments redacted).
- Output is JSON with a `summary` in Markdown, capped at 25 KB.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from ..runtime import NotAudited, Runtime

# stdout belongs to the MCP protocol; keep library chatter off stderr too.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

MAX_BYTES = 25_000
READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)

INSTRUCTIONS = """Agent Factory holds this repository's engineering memory: architecture, reusable implementations,
patterns, decisions and constraints, each backed by evidence. Before implementing a change call get_feature_context;
before creating any new class, service or module call find_reusable; before changing a shared symbol call impact_of.
Record durable findings with propose_memory (they start as candidates; humans or the code graph validate them)."""


def fit(result: dict[str, Any], limit: int = MAX_BYTES) -> dict[str, Any]:
    """Trim the largest lists in `data` until the JSON fits; say so in `next`."""
    text = json.dumps(result, default=str)
    if len(text) <= limit:
        return result
    data = result.get("data")
    trimmed: list[str] = []
    while len(json.dumps(result, default=str)) > limit and isinstance(data, dict):
        lists = [(k, v) for k, v in data.items() if isinstance(v, list) and len(v) > 1]
        if not lists:
            break
        key, value = max(lists, key=lambda kv: len(json.dumps(kv[1], default=str)))
        data[key] = value[: max(1, len(value) // 2)]
        trimmed.append(key)
    if len(json.dumps(result, default=str)) > limit:
        result["summary"] = str(result.get("summary", ""))[: limit // 2] + "\n…(truncated)"
        result["data"] = {"truncated": True}
    result["truncated"] = True
    result["next"] = (
        f"Output was trimmed to fit {limit // 1000} KB ({', '.join(sorted(set(trimmed))) or 'summary'}). "
        "Narrow the request (smaller budget, a specific symbol) for full detail."
    )
    return result


class RuntimeHolder:
    """Opens the runtime on the first tool call, so the MCP handshake never waits on the network."""

    def __init__(self, factory: Callable[[], Runtime]):
        self.factory = factory
        self._rt: Runtime | None = None

    def get(self) -> Runtime:
        if self._rt is None:
            try:
                self._rt = self.factory()
            except Exception as error:
                raise ToolError(f"Agent Factory is not ready: {error}. Run: agent-factory doctor") from error
        return self._rt

    def close(self) -> None:
        if self._rt is not None:
            self._rt.close()
            self._rt = None


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def build_server(factory: Callable[[], Runtime]) -> MCPServer:
    holder = RuntimeHolder(factory)
    server = MCPServer("agent-factory", instructions=INSTRUCTIONS)
    server._af_holder = holder  # type: ignore[attr-defined]  # tests and `serve` close it

    def rt() -> Runtime:
        return holder.get()

    def audited() -> Runtime:
        runtime = rt()
        try:
            runtime.require_audit()
        except NotAudited as error:
            raise ToolError(str(error)) from error
        return runtime

    async def traced(
        name: str, args: dict[str, Any], feature_id: str | None, work: Callable[[], Awaitable[dict[str, Any]]]
    ) -> dict[str, Any]:
        result = await work()
        try:
            runtime = rt()
            await runtime.features().record_step(
                feature_id,
                f"Called {name}",
                action=name,
                tool_name=name,
                arguments={k: v for k, v in args.items() if v is not None},
                result_summary=str(result.get("summary", ""))[:500],
            )
        except Exception:  # tracing must never break a tool
            pass
        if rt().warnings:
            result.setdefault("warnings", []).extend(dict.fromkeys(rt().warnings))
            rt().warnings.clear()
        return fit(result)

    # -- read tools ------------------------------------------------------------------------------------------------

    @server.tool(annotations=READ)
    async def get_feature_context(
        request: str, budget_tokens: int = 4000, feature_id: str | None = None
    ) -> dict[str, Any]:
        """Call FIRST for any feature, bug fix or refactor request, before reading or editing code.
        Returns the existing architecture, reusable implementations, patterns, decisions, constraints,
        related features and tests for the request, within a token budget."""
        from ..context.pack import render_markdown

        async def work() -> dict[str, Any]:
            pack = await audited().engine().abuild(request, budget=max(500, min(budget_tokens, 20_000)))
            # The Markdown summary is what agents read; the structured copy drops what only repeats it.
            data = pack.model_dump(
                mode="json", exclude={"docs": True, "memory": {"notes"}, "reusable": {"__all__": {"why", "doc"}}}
            )
            return {"summary": render_markdown(pack), "data": data}

        return await traced("get_feature_context", {"request": request}, feature_id, work)

    @server.tool(annotations=READ)
    async def get_memory_context(
        request: str, budget_tokens: int = 4000, feature_id: str | None = None
    ) -> dict[str, Any]:
        """Alias for get_feature_context: call before implementing a feature or bug fix to retrieve
        architecture, reusable implementations, patterns, decisions, constraints and tests."""
        return await get_feature_context(request=request, budget_tokens=budget_tokens, feature_id=feature_id)

    @server.tool(annotations=READ)
    async def find_reusable(
        name: str,
        description: str = "",
        methods: list[str] | None = None,
        role: str | None = None,
        feature_id: str | None = None,
    ) -> dict[str, Any]:
        """Call BEFORE creating any new class, service, repository, hook, component or module. Finds existing
        implementations to reuse or extend. Verdict: reuse | extend | new_ok. Never overrule `reuse` silently."""
        from ..context.reuse import ProposedAbstraction, render_reuse

        async def work() -> dict[str, Any]:
            from pydantic import ValidationError

            try:
                proposal = ProposedAbstraction(name=name, description=description, methods=methods or [], role=role)
            except ValidationError as error:
                raise ToolError(
                    f"invalid proposal: {error.errors()[0]['loc'][0]}: {error.errors()[0]['msg']}"
                ) from error
            report = audited().reuse().find(proposal)
            return {"summary": render_reuse(report), "data": _dump(report)}

        return await traced(
            "find_reusable", {"name": name, "description": description, "methods": methods}, feature_id, work
        )

    @server.tool(annotations=READ)
    async def impact_of(symbol_or_path: str, depth: int = 2, feature_id: str | None = None) -> dict[str, Any]:
        """Call before modifying a shared symbol or file. Lists dependents, the routes that reach it, tests to run,
        files that usually change with it, and the rules in force."""
        from ..context.impact import TargetNotFound, analyze, render_impact

        async def work() -> dict[str, Any]:
            runtime = audited()
            try:
                report = analyze(runtime.store, runtime.project_id, symbol_or_path, depth)
            except TargetNotFound as error:
                raise ToolError(f"{error}. Use a class/function name, Class.method, or a repo-relative path") from error
            return {"summary": render_impact(report), "data": _dump(report)}

        return await traced("impact_of", {"symbol_or_path": symbol_or_path}, feature_id, work)

    @server.tool(annotations=READ)
    async def get_constraints(scope: str | None = None) -> dict[str, Any]:
        """Rules this code must not violate (validated only). Optional scope: a path prefix, a role
        (Controller, Service...) or a module path."""
        runtime = audited()
        rows = runtime.store.read(
            """
            MATCH (k:Constraint {project_id: $p, status: 'validated'})
            OPTIONAL MATCH (s:Symbol)-[:CONSTRAINED_BY]->(k)
            WITH k, collect(s) AS scoped
            WHERE $scope IS NULL OR size(scoped) = 0
               OR any(s IN scoped WHERE s.path STARTS WITH $scope OR $scope IN s.roles)
            RETURN k.uid AS uid, k.title AS title, k.claim AS claim, k.rule_type AS rule_type, k.rule AS rule,
                   k.severity AS severity, k.confidence AS confidence
            ORDER BY k.confidence DESC
            """,
            p=runtime.project_id,
            scope=scope,
        )
        summary = (
            "\n".join(f"- **{r['title']}** ({r['severity'] or 'error'}, confidence {r['confidence']})" for r in rows)
            or "No validated constraints in scope."
        )
        return fit({"summary": summary, "data": {"constraints": rows}})

    @server.tool(annotations=READ)
    async def get_patterns(category: str | None = None, include_candidates: bool = False) -> dict[str, Any]:
        """Established implementation patterns with their evidence (validated; candidates on request)."""
        runtime = audited()
        statuses = ["validated", "candidate"] if include_candidates else ["validated"]
        rows = runtime.store.read(
            """
            MATCH (k:Pattern {project_id: $p}) WHERE k.status IN $statuses AND ($c IS NULL OR k.category = $c)
            OPTIONAL MATCH (k)-[:SUPPORTED_BY]->(e:Evidence)
            WITH k, collect(e.path + ':' + toString(coalesce(e.line_start, 1)))[..5] AS evidence
            RETURN k.uid AS uid, k.title AS title, k.claim AS claim, k.status AS status, k.category AS category,
                   k.confidence AS confidence, evidence ORDER BY k.confidence DESC
            """,
            p=runtime.project_id,
            statuses=statuses,
            c=category,
        )
        summary = (
            "\n".join(
                f"- **{r['title']}** ({r['status']}, {r['confidence']}) — {', '.join(r['evidence'][:3])}" for r in rows
            )
            or "No patterns found."
        )
        return fit({"summary": summary, "data": {"patterns": rows}})

    @server.tool(annotations=READ)
    async def search_memory(query: str, include_candidates: bool = False, limit: int = 10) -> dict[str, Any]:
        """Keyword search over project knowledge (patterns, decisions, constraints) plus the most relevant code
        symbols. Use for 'what do we know about X' questions."""
        from ..context.query import tokenize
        from ..context.retrievers import lucene_query

        runtime = audited()
        terms = tokenize(query) or [query]
        knowledge = runtime.store.read(
            "CALL db.index.fulltext.queryNodes('af_knowledge_text', $q) YIELD node, score "
            "WHERE node.project_id = $p AND (node.status = 'validated' OR ($cand AND node.status = 'candidate')) "
            "RETURN node.uid AS uid, node.kind AS kind, node.title AS title, node.status AS status, "
            "round(score, 3) AS score LIMIT $limit",
            q=lucene_query(terms),
            p=runtime.project_id,
            cand=include_candidates,
            limit=limit,
        )
        symbols = [u.rsplit("#", 1)[-1] for u in runtime.engine().retrieve(query).ranked(limit)]
        summary = "\n".join(
            [
                *(f"- {k['kind']}: **{k['title']}** ({k['status']})" for k in knowledge),
                "Relevant code: " + ", ".join(symbols) if symbols else "",
            ]
        ).strip()
        return fit({"summary": summary or "Nothing found.", "data": {"knowledge": knowledge, "symbols": symbols}})

    @server.tool(annotations=READ)
    async def ask_graph(question: str) -> dict[str, Any]:
        """Structured questions about the codebase graph (counts, who-depends-on, which routes...). Generates a
        read-only Cypher query, runs it, and returns the rows with the query."""
        from ..context.ask import UnsafeQuery, ask, graphrag_generator

        runtime = audited()
        generate = graphrag_generator(
            runtime.config.llm.model, runtime.env.get("OPENAI_API_KEY"), runtime.env.get("OPENAI_BASE_URL")
        )
        try:
            result = ask(runtime.store, runtime.project_id, question, generate)
        except UnsafeQuery as error:
            raise ToolError(str(error)) from error
        except Exception as error:
            body = getattr(error, "body", None)
            raise ToolError(f"could not answer: {body.get('message') if isinstance(body, dict) else error}") from error
        summary = f"```cypher\n{result.cypher}\n```\n" + "\n".join(json.dumps(r, default=str) for r in result.rows[:30])
        return fit({"summary": summary, "data": result.to_dict()})

    @server.tool(annotations=READ)
    async def how_did_we_handle(task: str) -> dict[str, Any]:
        """How similar tasks were done before in this repository, and whether they succeeded (reasoning memory)."""
        runtime = rt()
        traces = await runtime.memory.similar_traces(task, limit=5)
        if not getattr(runtime.memory, "enabled", False):
            return {"summary": "Agent memory is not configured (MVP_NEO4J_* / embeddings).", "data": {"traces": []}}
        rows = [{"task": t.task, "outcome": t.outcome, "success": t.success, "steps": t.steps[:10]} for t in traces]
        summary = "\n".join(
            f"- {r['task']} → {r['outcome'] or 'no outcome'}"
            + (" ✓" if r["success"] else " ✗" if r["success"] is False else "")
            for r in rows
        )
        return fit({"summary": summary or "No similar tasks recorded yet.", "data": {"traces": rows}})

    @server.tool(annotations=READ)
    async def how_did_i_handle(task: str) -> dict[str, Any]:
        """Alias for how_did_we_handle: retrieve how similar tasks were solved previously in this repository."""
        return await how_did_we_handle(task=task)

    @server.tool(annotations=READ)
    async def get_token_savings(request: str, budget_tokens: int = 4000) -> dict[str, Any]:
        """Calculate the token economy and surgical retrieval efficiency for a given feature request."""
        pack = await audited().engine().abuild(request, budget=max(500, min(budget_tokens, 20_000)))
        if pack.token_economy is None:
            return fit({"summary": "Token economy metrics not available for this pack.", "data": None})
        summary = f"{pack.token_economy.to_markdown_badge()}\n\n{pack.token_economy.to_markdown_table()}"
        return fit({"summary": summary, "data": pack.token_economy.to_dict()})

    @server.tool(annotations=READ)
    async def get_feature(feature_id: str | None = None) -> dict[str, Any]:
        """The current feature session (or the one given): request, plan, status and step count."""
        from ..workflow.feature import FeatureNotFound, render_plan

        runtime = rt()
        try:
            state = runtime.features().active(feature_id)
        except FeatureNotFound as error:
            raise ToolError(str(error)) from error
        if state is None:
            return {"summary": "No active feature on this branch. Call start_feature.", "data": None}
        return fit({"summary": render_plan(state), "data": _dump(state)})

    # -- write tools (safe writes only) ----------------------------------------------------------------------------

    @server.tool(annotations=WRITE)
    async def start_feature(
        name: str, request: str, branch: str | None = None, budget_tokens: int = 4000
    ) -> dict[str, Any]:
        """Start a feature session (short-term task memory) and get its context pack. Every later tool call on this
        branch is recorded with the feature."""
        from ..context.pack import render_markdown

        runtime = audited()
        state = await runtime.features().start(name, request, branch)
        pack = await runtime.engine().abuild(request, budget=budget_tokens)
        summary = f"Feature **{state.name}** started as `{state.feature_id}`.\n\n" + render_markdown(pack)
        # The pack is in the summary; the structured copy is one get_feature_context call away.
        return fit({"summary": summary, "data": {"feature": _dump(state), "reusable": [s.name for s in pack.reusable]}})

    @server.tool(annotations=WRITE)
    async def record_plan(plan: dict[str, Any], feature_id: str | None = None) -> dict[str, Any]:
        """Store the implementation plan (spec §18) for the active feature: reuse decisions, planned files and
        modules, new abstractions with a justification each, new dependencies, cited knowledge uids, risks."""
        from pydantic import ValidationError

        from ..workflow.feature import FeatureNotFound, FeaturePlan, render_plan

        runtime = rt()
        try:
            state = await runtime.features().record_plan(feature_id, FeaturePlan.model_validate(plan))
        except ValidationError as error:
            raise ToolError(f"invalid plan: {error}") from error
        except FeatureNotFound as error:
            raise ToolError(str(error)) from error
        return fit({"summary": render_plan(state), "data": _dump(state)})

    @server.tool(annotations=WRITE)
    async def record_step(
        thought: str, action: str | None = None, result_summary: str | None = None, feature_id: str | None = None
    ) -> dict[str, Any]:
        """Record a reasoning step the tool calls do not capture (e.g. 'tried X, failed because Y')."""
        state = await rt().features().record_step(feature_id, thought, action=action, result_summary=result_summary)
        if state is None:
            raise ToolError("no active feature on this branch; call start_feature first")
        return {"summary": f"Step {state.steps} recorded for {state.feature_id}.", "data": {"steps": state.steps}}

    @server.tool(annotations=WRITE)
    async def propose_memory(
        kind: str,
        title: str,
        claim: str,
        evidence: list[str],
        rationale: str | None = None,
        rule: dict[str, Any] | None = None,
        feature_id: str | None = None,
    ) -> dict[str, Any]:
        """Propose durable project knowledge (pattern | decision | constraint) with evidence as path[:start[-end]].
        It is validated like everything else and starts as a candidate; it becomes a rule only when the code graph
        re-derives it or a human approves it. Optional rule for constraints: {"type": "forbid_dependency",
        "from_role": ..., "to_roles": [...]} (also restrict_access, forbid_external_dep, placement)."""
        from pydantic import ValidationError

        from ..memory.validation import EvidenceRef, Proposal

        async def work() -> dict[str, Any]:
            runtime = audited()
            rule_body = dict(rule or {})
            rule_type = rule_body.pop("type", None)
            try:
                proposal = Proposal.model_validate(
                    {
                        "kind": kind,
                        "title": title,
                        "claim": claim,
                        "source": "agent",
                        "rationale": rationale,
                        "evidence": [EvidenceRef.parse(e) for e in evidence],
                        "rule_type": rule_type,
                        "rule": rule_body or None,
                    }
                )
            except (ValidationError, ValueError) as error:
                raise ToolError(f"invalid proposal: {error}") from error
            result = runtime.knowledge().submit(proposal, "agent:mcp")
            state = runtime.features().active(feature_id) if result.uid else None
            if state is not None and result.uid:
                runtime.features().repo.link(state.uid, "CREATED", [result.uid])
            lines = [f"{result.outcome}: {result.uid or '-'} → {result.status or '-'} (confidence {result.confidence})"]
            lines += [f"- {r}" for r in result.reasons + result.dropped_evidence]
            return {"summary": "\n".join(lines), "data": result.to_dict()}

        return await traced("propose_memory", {"kind": kind, "title": title}, feature_id, work)

    @server.tool(annotations=READ)
    async def check_changes(feature_id: str | None = None, base: str | None = None) -> dict[str, Any]:
        """Run the anti-slop guardrails (duplication, architecture, scope, complexity...) on the current diff."""
        from ..guardrails.diff import analyze_diff
        from ..guardrails.rules import run_guardrails

        async def work() -> dict[str, Any]:
            runtime = rt()
            state = runtime.features().active(feature_id)
            base_sha = base or (state.base_sha if state else None)
            diff = analyze_diff(runtime.root, base_sha=base_sha, project_id=runtime.project_id)
            report = run_guardrails(
                diff,
                runtime,
                plan=state.plan if state else None,
                waivers=state.waivers if state else None,
                test_baseline=state.test_baseline if state else None,
                feature_id=state.feature_id if state else None,
            )
            return {"summary": report.to_markdown(), "data": report.model_dump(mode="json")}

        return await traced("check_changes", {"feature_id": feature_id, "base": base}, feature_id, work)

    @server.tool(annotations=WRITE)
    async def add_evidence(kind: str, path: str, summary: str, feature_id: str | None = None) -> dict[str, Any]:
        """Register an evidence artifact (test output, screenshot, recording) for the active feature."""

        async def work() -> dict[str, Any]:
            runtime = rt()
            state = runtime.features().active(feature_id)
            if state is None:
                raise ToolError("no active feature on this branch; call start_feature first")
            src_path = Path(path)
            if not src_path.is_absolute():
                src_path = runtime.root / src_path
            try:
                item = runtime.evidence(state.feature_id).add_artifact(kind, src_path, summary)
            except Exception as error:
                raise ToolError(f"could not register evidence: {error}") from error
            return {
                "summary": f"Evidence registered: {item.id} ({item.path}) [SHA-256: {item.sha256[:10]}...]",
                "data": item.model_dump(mode="json"),
            }

        return await traced("add_evidence", {"kind": kind, "path": path}, feature_id, work)

    @server.tool(annotations=WRITE)
    async def complete_feature(
        outcome: str = "success", pr_url: str | None = None, feature_id: str | None = None
    ) -> dict[str, Any]:
        """Finish the feature: commit what it changed into project memory and close its reasoning trace."""
        from ..workflow.feature import FeatureNotFound

        async def work() -> dict[str, Any]:
            runtime = audited()
            try:
                result = await runtime.features().complete(feature_id, outcome=outcome, pr_url=pr_url)
            except FeatureNotFound as error:
                raise ToolError(str(error)) from error
            except Exception as error:
                raise ToolError(f"could not complete feature: {error}") from error
            return {"summary": result["summary"], "data": result}

        return await traced("complete_feature", {"outcome": outcome, "pr_url": pr_url}, feature_id, work)

    # -- resources & prompts --------------------------------------------------------------------------------------

    @server.resource("af://constraints", mime_type="text/markdown", description="Validated constraints")
    async def constraints_resource() -> str:
        return str((await get_constraints())["summary"])

    @server.resource("af://patterns", mime_type="text/markdown", description="Validated patterns")
    async def patterns_resource() -> str:
        return str((await get_patterns())["summary"])

    @server.prompt(description="Plan a feature with Agent Factory context (spec §18)")
    async def plan_feature(request: str) -> str:
        return (
            f"Plan this change before editing any file: {request}\n\n"
            "1. Call get_feature_context with the request.\n"
            "2. For every new abstraction you consider, call find_reusable and record the verdict.\n"
            "3. Call impact_of for each shared symbol you intend to change.\n"
            "4. Fill in the FEATURE PLAN skeleton from the context and store it with record_plan."
        )

    return server


def serve(root: Path, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8765) -> None:
    server = build_server(lambda: Runtime.open(root))
    try:
        if transport == "stdio":
            server.run("stdio")
        else:
            import anyio

            anyio.run(lambda: server.run_streamable_http_async(host=host, port=port))
    finally:
        server._af_holder.close()  # type: ignore[attr-defined]
