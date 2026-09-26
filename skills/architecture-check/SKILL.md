---
name: architecture-check
description: >
  Check a change against the repository's validated constraints and patterns before it is proposed:
  layer boundaries, duplication, new dependencies, scope. Use when implementation is done and
  before verification or a PR; whenever code crosses a layer (a controller touching a repository, a route
  touching the database); when adding a dependency; and when the diff grew beyond the plan.
license: MIT
compatibility: >
  Needs the agent-factory MCP server or CLI and an audited repository. The automatic guardrail run
  (check_changes) arrives with Agent Factory's guardrail engine; until then this skill walks the same
  checks with the retrieval tools.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(git diff*) Bash(git status*) mcp__agent-factory__get_constraints mcp__agent-factory__check_changes mcp__agent-factory__find_reusable mcp__agent-factory__impact_of
---

# Architecture check

Working code can still make the codebase worse. This check asks the questions a senior reviewer
would ask, using the repository's own rules instead of taste.

## Steps

1. Call `check_changes` for the active feature. If it answers `not_available_yet`, do steps 2–5 by hand.
2. **Constraints**: call `get_constraints` (optionally with the paths you changed as `scope`) and
   compare each validated rule with your diff (`git diff`). A validated `forbid_dependency` or
   `restrict_access` rule is a hard stop.
3. **Duplication**: for every class or function you added, call `find_reusable` with its name and
   methods. A `reuse` verdict against something you just wrote means you duplicated it.
4. **Complexity**: list new entries in package.json / pyproject.toml. A validated
   `forbid_external_dep` constraint for that category means remove it or ask the developer.
5. **Scope**: compare `git diff --name-only` with the plan's planned files. Explain every extra file
   or revert it.
6. Fix what fails, or record a waiver with its reason (`record_step`) and mention it in the PR.

## The questions (spec §22)

- Duplication: does equivalent functionality already exist?
- Abstraction: is each new abstraction necessary?
- Architecture: does it follow the existing boundaries?
- Reusability: could another feature reuse it?
- Consistency: does it follow validated patterns?
- Complexity: did it add unnecessary infrastructure?
- Scope: did it modify unrelated areas?
- Regression: do the existing tests still pass?
