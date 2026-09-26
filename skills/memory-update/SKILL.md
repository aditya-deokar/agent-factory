---
name: memory-update
description: >
  Record durable knowledge you discover while working, with evidence, so the next session starts
  smarter. Use when you learn how this repository does something that is not in memory yet ("all
  outbound HTTP goes through integrations/"), when an attempt fails for a reason worth remembering,
  and when you make a decision future work should follow. Skip for facts that only matter to this task.
license: MIT
compatibility: >
  Needs the agent-factory MCP server or CLI and an audited repository. Proposals become candidates;
  a human or the code graph validates them.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) mcp__agent-factory__propose_memory mcp__agent-factory__record_step mcp__agent-factory__search_memory
---

# Memory update

Memory should grow while you work, not only after the PR. But memory is not truth: everything you
propose starts as a candidate and needs evidence.

## Steps

1. Check it is new: call `search_memory` with the idea (include candidates). If it exists, do not repeat it.
2. Propose it with evidence as `path:start-end`:

   ```bash
   agent-factory memory propose --source agent --kind pattern --title "Outbound HTTP goes through integrations" --claim "Every external API call is wrapped by a class in src/integrations/" --evidence src/integrations/email.integration.ts:7-15 --evidence src/services/email.service.ts:9-16
   ```

   With MCP: `propose_memory` with the same fields. For a rule the graph can check, add a `rule`
   (`forbid_dependency`, `restrict_access`, `forbid_external_dep`, `placement`); it is re-derived
   from the code and can become validated without a human if the code proves it.
3. For failures and dead ends, record a step instead: `record_step` ("tried X, failed because Y").

## Rules

- One claim per proposal, in one or two sentences, with at least one evidence reference.
- Never put credentials, tokens or personal data in a claim. Proposals containing secrets are rejected.
- Do not re-propose something a human rejected. It is refused unless you bring new evidence.
