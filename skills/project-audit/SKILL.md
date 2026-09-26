---
name: project-audit
description: >
  Build this repository's engineering memory and review what was found. Use when Agent Factory is
  used in a repository for the first time, when asked to "understand this repository" or map its architecture,
  after a large refactor or merge, and when memory looks stale. The audit observes only: it never
  modifies production code.
license: MIT
compatibility: >
  Needs the agent-factory CLI on PATH, a reachable Neo4j (agent-factory doctor), and ideally the MCP
  server for the review steps. TypeScript/JavaScript and Python are parsed; other files are skipped.
metadata:
  author: agent-factory
  version: "1.0"
allowed-tools: Bash(agent-factory *) Bash(git log*) Read mcp__agent-factory__get_patterns mcp__agent-factory__get_constraints mcp__agent-factory__propose_memory mcp__agent-factory__impact_of
---

# Project audit

Observe → Analyze → Map → Extract → Validate → Store. The deterministic auditor does the mapping;
you add judgment: what the patterns mean, which candidates are real, what the code does not say.

## Steps

1. Check the harness, then audit:

   ```bash
   agent-factory doctor
   agent-factory audit --json
   ```

   `audit` is incremental after the first run. Use `--full` after big refactors, `--no-embed` offline.
2. Read the summary: roles found, edges, ADRs, and the **candidates to review**.
3. Review candidates one at a time (see the references for what to look for):
   - list them: `agent-factory memory list --status candidate --json`
   - inspect one with its evidence and counter-examples: `agent-factory memory show <id> --json`
   - open the evidence files. Does the claim hold? Is each counter-example a real violation or noise?
   - recommend approve or reject to the developer, with a reason. Only humans approve
     (`agent-factory memory approve <id>`), unless a rule re-derives from the code by itself.
4. Add what the auditor cannot see, as candidates with evidence (the `memory-update` skill):
   conventions stated nowhere, intent behind unusual structure, decisions only visible in history
   (`git log --oneline -- <path>`).
5. Summarise the architecture for the developer: layers, main flows, the validated rules, and the
   known violations worth fixing.

## References

- `references/architecture-map.md`: layers, modules and flows to confirm.
- `references/pattern-discovery.md`: judging pattern candidates and counter-examples.
- `references/decision-extraction.md`: turning ADRs, docs and history into decisions.
- `references/relationship-mapping.md`: what the graph links mean and how to query them.
