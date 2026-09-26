---
name: memory-retrieval
description: >
  Load what this repository already knows before you change it: existing architecture, reusable
  implementations, patterns, decisions, constraints, related features and the tests to run. Use when
  starting any feature, bug fix, refactor or "how does X work here" question, before reading or
  editing code, and again when the task shifts to a different area of the codebase. Skip only for
  edits that touch no code (typos in docs, formatting).
license: MIT
compatibility: >
  Needs the agent-factory MCP server (preferred) or the agent-factory CLI on PATH, and an audited
  repository (agent-factory audit). Works in Claude Code, Cursor, Codex, VS Code and any agent that
  reads AGENTS.md.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) mcp__agent-factory__get_feature_context mcp__agent-factory__how_did_we_handle mcp__agent-factory__search_memory
---

# Memory retrieval

A codebase remembers more than any one session can. Read that memory before you form a plan,
not after you have written the code.

## Steps

1. Call `get_feature_context` with the request in the user's words.
   CLI fallback:

   ```bash
   agent-factory context "Add team invitations" --json
   ```

2. Read the pack in this order and keep it in mind for the whole task:
   - **Constraints**: rules you must not break. They are validated, with evidence.
   - **Reusable implementations**: what already does part of the job, who uses it, which methods it has.
   - **Patterns** and **Decisions**: how this repository does things, and why.
   - **Relevant architecture**: the flow through the layers you will touch.
   - **Relevant tests**: what to run before and after.
   - **Unverified observations**: candidates, not rules. Treat them as hints, never as requirements.
3. Ask memory how similar work went before:
   call `how_did_we_handle` with a short task description (skip if agent memory is not configured).
4. If the pack says it was trimmed, call again with a larger `budget_tokens`, or narrow the request.
5. Carry the pack into the `feature-planning` skill. Do not start editing yet.

## Rules

- Never contradict a validated constraint without saying so to the developer, with the reason.
- When the pack shows an existing implementation for what you were about to write, switch to the
  `reuse-check` skill before designing anything new.
- Quote evidence as `path:line` when you rely on a pattern or decision, so the developer can check it.
- If the tool says the repository has not been audited, tell the developer to run
  `agent-factory audit` (or run it yourself if you are allowed to run commands). Do not guess the
  architecture instead.
