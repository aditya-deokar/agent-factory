---
name: memory-commit
description: >
  Close a feature by committing what it changed into project memory (new relationships, reused and
  introduced symbols, patterns that gained support, decisions made), so the next feature starts with it.
  Use when the feature's PR is merged or the developer says the feature is finished, and when
  a feature is abandoned, to record why.
license: MIT
compatibility: >
  Needs the agent-factory CLI or MCP server and an audited repository. The one-call commit
  (complete_feature) arrives with Agent Factory's evidence engine; until then an incremental audit
  plus a closing step records the same knowledge.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(git *) mcp__agent-factory__complete_feature mcp__agent-factory__record_step mcp__agent-factory__propose_memory mcp__agent-factory__get_feature
---

# Memory commit

Every feature should leave the repository smarter than it found it (spec §25).

## Steps

1. Call `complete_feature` with the outcome (`success` or `abandoned`) and the PR URL.
   If it answers `not_available_yet`:
   - refresh the code graph: `agent-factory audit` (incremental, only changed files);
   - record the outcome as the last step: `agent-factory feature step "Completed: <outcome>, PR <url>"`;
   - propose the knowledge the feature established (the `memory-update` skill): for example
     "Invitations reuse TokenService with purpose team_invite", with the new code as evidence.
2. If the feature made an architectural decision, suggest an ADR in `docs/adr/`: the next audit turns
   it into validated knowledge.
3. Tell the developer what memory gained: new candidates to review
   (`agent-factory memory list --status candidate`), and any knowledge that was deprecated because the
   code no longer supports it.

## Rules

- An abandoned feature is still worth a closing step: why it was abandoned helps the next attempt.
- Do not approve your own proposals. Humans, or rules the code graph re-derives, validate knowledge.
