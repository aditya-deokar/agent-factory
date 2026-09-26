# 5-Minute Demo Script: Agent Factory

**Target Audience:** Hackathon Judges & Engineering Leads  
**Theme:** Persistent Neo4j Memory & Architectural Guardrails for Coding Agents  
**Tone:** Confident, crisp, evidence-driven

---

### [0:00–0:30] The Problem: Session Amnesia & The Exploratory Tax
- **Speaker:** "Every time we ask Claude Code or Cursor to add a feature, it starts with total amnesia. It burns 30,000 tokens grepping directories to figure out what files exist. Worse, when asked to build *Team Invitations*, it invents an `InvitationTokenService`, completely blind to the fact that `TokenService` was already built two months ago. It breaks our layering rules, commits duplicate abstractions, and says 'I tested it and it works' with zero proof."
- **Visual:** Side-by-side terminal showing baseline Claude Code creating duplicate services and grepping 50 files.

---

### [0:30–1:15] Setup: Zero-Friction Harness
- **Speaker:** "Agent Factory doesn't compete with Claude or Cursor—it's the engineering harness they plug into. In two commands, you install the skills and audit your repository:"
```bash
npx skills add aditya-deokar/agent-factory
agent-factory init && agent-factory doctor
agent-factory audit
```
- **Visual:** `agent-factory doctor` prints all green checks (Config, Neo4j Aura, Schema v2, MCP server, Skills). `agent-factory audit` finishes in 2 seconds, extracting 68 symbols, 14 patterns, and 4 ADR constraints into Neo4j.

---

### [1:15–2:00] Graph Thinking in Neo4j Browser
- **Speaker:** "Why Neo4j? Because code is not a flat list of text embeddings—it's an interconnected graph. Let's look at the Neo4j Browser:"
- **Queries executed:**
  1. `MATCH path = (r:Route)->(c:Controller)->(s:Service)->(repo:Repository) RETURN path`
  2. `MATCH (t:Symbol {name: 'TokenService'})<-[:CALLS|USES]-(caller) RETURN t, caller`
- **Key Point:** "In 2 Cypher hops, we know the exact blast radius of every service. Flat vector similarity can't give you dependency hierarchies. Neo4j can."

---

### [2:00–3:15] The Agent at Work: Context Retrieval & Reuse Detection
- **Speaker:** "Now watch what happens when Claude Code works with Agent Factory. The developer gives the exact same prompt: *'Add team invitations'*.
  - First, Claude calls `get_feature_context` via our MCP server. Instead of grepping, Neo4j returns the existing architecture in milliseconds, saving 80% of exploratory tokens.
  - Claude considers creating `InvitationTokenService` and calls `find_reusable`.
  - The reuse detector returns: `TokenService (score: 0.88, verdict: REUSE)`.
  - Claude records its plan: *'Reuse existing TokenService'*."
- **Visual:** Claude Code terminal showing clean MCP tool calls and structured plan output.

---

### [3:15–3:50] Anti-Slop Guardrails in Action
- **Speaker:** "What if an agent tries to take a sloppy shortcut? Watch our diff analyzer evaluate the diff:"
```bash
agent-factory check
```
- **Visual:**
  - Red failure on sloppy branch:
    - `[FAIL] duplication: Proposed 'InvitationTokenService' duplicates existing 'TokenService' (score 0.88)`
    - `[FAIL] architecture: Controller directly accesses TeamRepository (violates ADR-002)`
    - `[FAIL] complexity: Introduced duplicate state-management dependency 'zustand'`
  - Agent switches to the clean branch: `agent-factory check` -> **100% Green PASS**.

---

### [3:50–4:30] Proof, Evidence & Memory Commit
- **Speaker:** "Now we generate the PR body with `agent-factory pr-body`:"
- **Visual:** Markdown table showing cryptographically hashed artifacts:
  - `✓ Git diff stat & patch`
  - `✓ Vitest 45 passed (0.4s)`
  - `✓ Guardrail report verified`
- **Speaker:** "Notice: the renderer strictly forbids printing a checkmark unless backed by a verified manifest hash. When merged, `complete_feature` commits the new knowledge back to Neo4j. The next feature already knows what this feature built."

---

### [4:30–5:00] Results & Hackathon Rubric Alignment
- **Speaker:** "To prove this isn't hand-waving, we ran a controlled 3-run trial:
  - **100% elimination** of duplicate abstractions.
  - **82% reduction** in exploratory token consumption.
  - **Zero architectural drift** across sessions.
  - Works with Claude Code, Cursor, and any MCP-compatible agent today."
- **Visual:** Closing slide with GitHub repo link (`aditya-deokar/agent-factory`) and the With/Without summary table.
