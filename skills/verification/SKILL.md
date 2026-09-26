---
name: verification
description: >
  Prove a change works and stays within the architecture before claiming it is done: run the
  guardrails and the project's checks, and collect evidence for every claim. Use before saying a task
  is finished, before opening or updating a PR, and when asked "does it work?". Skip only for changes
  with no behavior (comments, docs).
license: MIT
compatibility: >
  Needs the agent-factory CLI or MCP server, the project's own test/build commands (agent-factory.yaml
  checks), and the Software Factory test-evidence and visual-diff skills for recordings and screenshots.
  The evidence engine (evidence collect, add_evidence) arrives with Agent Factory's guardrail engine;
  until then capture the outputs yourself as described below.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(npm test*) Bash(npm run*) Bash(pytest*) Bash(uv run*) mcp__agent-factory__check_changes mcp__agent-factory__add_evidence
---

# Verification

A feature is complete when a skeptical reviewer can check it without re-doing your work.

## Steps

1. Run the guardrails: `check_changes`. If it answers `not_available_yet`, run the
   `architecture-check` skill instead.
2. Run the project's checks (the commands in `agent-factory.yaml` under `checks`: test, build, lint).
   Every one must pass. Keep the output.
3. Collect evidence:
   - with the evidence engine: `agent-factory evidence collect`, and `add_evidence` for each
     screenshot or recording;
   - until it is available: save each command's output under `.agent-factory/evidence/<feature>/`
     and note the command, the exit code and the date.
4. For visible behavior use `test-evidence` (a recording) and `visual-diff` (before/after screenshots).
   For behavior with no visible surface, record request/response pairs or command output, before and after.
5. Run the tests of every dependent symbol listed by `impact_of` for what you changed.

## Rules

- Never claim a check passed that you did not run in this session.
- A failing check is reported with its output, not summarised away.
