---
name: pr-evidence
description: >
  Write the pull request body from evidence: what was implemented, what was reused, tests, evidence,
  decisions, memory updates, and the guardrail results, with no claim that is not backed by an artifact.
  Use when opening or updating a PR for a feature built with Agent Factory, and when asked to write a PR
  description or summary of the change.
license: MIT
compatibility: >
  Needs the agent-factory CLI or MCP server and gh for opening the PR. The generated body
  (agent-factory pr-body) arrives with Agent Factory's evidence engine; until then fill the template
  below yourself. Run the prose-cleanup skill over the result and code-review-loop after opening.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(gh pr*) Bash(git *) mcp__agent-factory__get_feature
---

# PR evidence

The PR body is a set of claims. Each one points at evidence or is marked as missing.

## Steps

1. Generate the body: `agent-factory pr-body --feature <id> --out pr.md`. If that command is not
   available yet, fill the template below from `get_feature` (plan, reuse decisions), your test
   output and the evidence you collected.
2. Run `prose-cleanup` over the body.
3. Open the PR (`gh pr create --body-file pr.md`), then run `code-review-loop`.

## Template (spec §24)

```text
# <Feature name>

## Implementation
- <what changed, per layer>

## Architectural Reuse
Existing: <symbols reused or extended, from the plan's reuse decisions>
<"No duplicate abstraction introduced." ONLY if the duplication check passed>

## Tests
<✓ or ✗ per check, with the command>

## Evidence
<✓ per artifact that exists; "✗ missing" otherwise>

## Architectural Decisions
<ADRs or decisions cited or created>

## Memory Updates
<knowledge proposed or created by this feature>

## Guardrails
<result per guardrail, including waivers and their reasons>
```

## Rules

- Never print ✓ for something you cannot link or paste.
- Mention every waiver and every validated constraint you had to discuss with the developer.
