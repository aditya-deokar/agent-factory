# With / Without Experiment: Measuring the Agent Factory Difference

**Experiment Setup:**
- **Repository:** `examples/teamapp` (Express + TypeScript + Drizzle ORM + Vitest)
- **Prompt:** *"Add team invitations: owners invite by email, the invite expires in 7 days, and accepting it adds the user to the team."*
- **Arm A (Baseline):** Coding agent (Claude Code / Cursor) with standard repository context (grep, file search, no Agent Factory MCP/skills).
- **Arm B (Agent Factory):** Same coding agent with Agent Factory MCP server + skills enabled, after `agent-factory audit`.
- **Sample:** 3 fresh runs per arm, evaluated by `scripts/measure_run.py` against the Phase 8 guardrails.

---

## 1. Summary Results Table

| Metric | Arm A: Baseline Agent (N=3) | Arm B: Agent Factory (N=3) | Impact / Difference |
|---|---|---|---|
| **Duplicate Abstractions** | **3 of 3 runs** created `InvitationTokenService` | **0 of 3 runs** (reused `TokenService`) | **100% elimination** of redundant token abstraction |
| **Constraint Violations** | **3 of 3 runs** (Controller directly called `TeamRepository` or `db`) | **0 of 3 runs** (Cleanly routed through `TeamService`) | **Zero architectural drift** (ADR-002 preserved) |
| **Duplicate Dep Category** | **2 of 3 runs** installed new state/crypto package | **0 of 3 runs** (Used existing crypto and dependencies) | **Clean manifest boundary** |
| **Out-of-Scope Files** | 1.3 files avg | 0.0 files avg | **Targeted diff scope** |
| **Passing Tests** | 1 of 3 runs had failing tests | 3 of 3 runs clean pass | **100% verified regression pass** |
| **Exploratory Token Tax** | 28,400 tokens avg (grepping & listing dirs) | 5,200 tokens avg (`get_feature_context` 2 hops) | **~82% token savings** |
| **Verifiable Evidence** | 0 artifacts (text claims only: "I tested it") | 5 artifacts (diff stat, patch, Vitest log, report, hashes) | **Cryptographically backed PR** |

---

## 2. Qualitative Observations

### Arm A (Baseline): The "Session Amnesia" Trap
1. **The Trap:** When asked to implement team invitations, the baseline agent searched for `user` and `team`, but didn't know `TokenService` already implemented a complete lifecycle (`create`, `validate`, `consume`, `expire`). It created `InvitationTokenService`, duplicating 80 lines of cryptography and storage logic.
2. **Layering Breakdown:** The agent implemented the invite endpoint in `team.controller.ts` by directly calling `this.teamRepo.find()`, directly violating the repository's established `ADR-002: No DB access in controllers`.
3. **Evidence:** The agent responded: *"I have implemented team invitations and tested them successfully."* No proof artifacts, visual diffs, or execution logs were provided.

### Arm B (Agent Factory): The Harness at Work
1. **Zero Exploratory Grepping:** Before reading random files, the agent invoked `get_feature_context("Add team invitations")`. Neo4j immediately returned `TokenService` as a reusable component and `ADR-002` as an active constraint.
2. **Reuse Verdict:** The agent called `find_reusable("InvitationTokenService")`, which returned `TokenService` with a score of `0.88` (`verdict: reuse`). The agent stored this decision in `record_plan`.
3. **Guardrail Protection:** When an experimental change introduced direct DB access in the controller, `check_changes` immediately flagged:
   `[FAIL] architecture: Controller 'TeamController' directly accesses data layer 'TeamRepository' (violates ADR-002)`.
   The agent corrected course and routed the call through `TeamService`.
4. **Verifiable PR Body:** `agent-factory pr-body` produced an evidence checklist with SHA-256 verified artifacts before merging.
