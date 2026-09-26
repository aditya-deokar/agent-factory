---
name: implementation-workflow
description: >
  The end-to-end way to implement a feature in a repository that uses Agent Factory: retrieve memory,
  plan, check reuse, isolate, build, record what you learn, check architecture, prove it, open the PR,
  commit memory. Use when asked to implement, add, build or change a feature, or to fix a bug that
  needs more than a one-line change. It chains the other skills; follow it in order.
license: MIT
compatibility: >
  Needs the agent-factory MCP server or CLI, an audited repository, git, and the Software Factory
  skills (worktree-isolation, service-layer, test-evidence, visual-diff, code-review-loop, prose-cleanup).
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(git *) mcp__agent-factory__get_feature_context mcp__agent-factory__find_reusable mcp__agent-factory__start_feature mcp__agent-factory__record_plan mcp__agent-factory__record_step mcp__agent-factory__propose_memory mcp__agent-factory__check_changes mcp__agent-factory__complete_feature
---

# Implementation workflow

You write the code. Agent Factory makes sure you understand the codebase first, reuse what exists,
respect its decisions, prove your work, and leave the repository smarter (spec §17).

## The sequence

| # | Step | Skill | Done when |
|---|---|---|---|
| 1 | Retrieve memory | `memory-retrieval` | You know the constraints, reusable code, patterns and tests in play |
| 2 | Isolate | `worktree-isolation` | You are on a fresh branch, never on main |
| 3 | Plan | `feature-planning` | `start_feature` + `record_plan` done; plan shown if large |
| 4 | Check reuse | `reuse-check` | Every new abstraction has a verdict recorded in the plan |
| 5 | Build | `service-layer` | Code follows the validated patterns cited in the plan |
| 6 | Record knowledge | `memory-update` | New durable facts proposed with evidence; failures recorded as steps |
| 7 | Check architecture | `architecture-check` | Constraints, duplication, dependencies and scope are clean or waived with a reason |
| 8 | Prove | `verification` (+ `test-evidence`, `visual-diff`) | Tests pass and evidence exists for every claim |
| 9 | Ship | `pr-evidence`, then `code-review-loop` | PR opened with the evidence-backed body; review clean |
| 10 | Commit memory | `memory-commit` | The feature's knowledge is in project memory |

## Rules

- Do not edit code before steps 1, 3 and 4.
- When reality diverges from the plan, update the plan (`record_plan`) or record a step saying why.
- A validated constraint is not yours to break. Stop and ask.
- "Implemented and tested" is not a result. The evidence is (spec §23).
