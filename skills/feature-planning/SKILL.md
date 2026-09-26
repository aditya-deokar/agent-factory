---
name: feature-planning
description: >
  Write an architecture-aware plan before editing: what exists, what gets reused, which files change,
  which new abstractions are really needed, what decision you are making, and the risks. Use when
  memory-retrieval is done and before the first edit of any feature or non-trivial fix; when a task will touch
  more than one layer (route, controller, service, repository, model); and when the user asks for a
  plan or design. Skip for one-line fixes.
license: MIT
compatibility: >
  Needs the agent-factory MCP server or the agent-factory CLI on PATH, and an audited repository.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(git branch*) mcp__agent-factory__start_feature mcp__agent-factory__record_plan mcp__agent-factory__get_feature
---

# Feature planning

The plan exists to stop you from jumping straight into code. It is short, concrete, and based on
the memory you just retrieved.

## Steps

1. Make sure you are on the feature's branch (the `worktree-isolation` skill creates one).
2. Start a session: call `start_feature` with a short name and the full request. It returns the
   context pack too, and every later tool call on this branch is remembered with the feature.
   CLI fallback:

   ```bash
   agent-factory feature start "Team invitations" --request "Add team invitations" --json
   ```

3. Fill in the plan using the template in `references/plan-template.md`:
   - **Existing Architecture**: copy the relevant flows from the pack.
   - **Reuse decisions**: one per proposed abstraction, from the `reuse-check` skill.
   - **Potential Files**: every file you expect to change, and the tests.
   - **New Abstractions**: only what `reuse-check` returned `new_ok` for, each with a justification.
   - **New Dependencies**: external packages. A validated "single library" constraint means no.
   - **Cited knowledge**: the uids of the patterns, decisions and constraints you are following.
   - **Architectural Decision** and **Risks**.
4. Store it: `record_plan` with the plan as JSON (CLI fallback: `agent-factory feature plan --file plan.json`).
5. Show the plan to the developer when the change is large or crosses a constraint. Then implement.

## Rules

- A plan with "New Abstractions" that were not checked by `reuse-check` is not finished.
- If the plan needs to break a validated constraint, stop and ask. Do not plan around it quietly.
- Keep the plan honest: when the implementation deviates, record a step saying so
  (`record_step`, or `agent-factory feature step "..."`).
