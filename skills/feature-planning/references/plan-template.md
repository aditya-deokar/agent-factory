# Plan template (spec §18)

Use this shape for `record_plan` (MCP) or `agent-factory feature plan --file plan.json`.

```json
{
  "summary": "Team invitations reuse TokenService with purpose team_invite and EmailService for delivery.",
  "existing_architecture": [
    "TeamController -> TeamService -> TeamRepository -> teams, team_members",
    "PasswordResetService -> TokenService, EmailService (same token lifecycle)"
  ],
  "reuse_decisions": [
    {"proposed": "InvitationTokenService", "verdict": "reuse", "chosen": "TokenService",
     "justification": "TokenService already covers create/validate/consume/expire; add purpose team_invite."}
  ],
  "planned_files": [
    "src/services/team.service.ts",
    "src/controllers/team.controller.ts",
    "src/routes/teams.routes.ts",
    "src/validators/team.schema.ts",
    "tests/team.service.test.ts"
  ],
  "planned_modules": ["src/services", "src/controllers", "src/routes"],
  "new_abstractions": [],
  "new_dependencies": [],
  "cited_knowledge": ["<project>:k:decision:adr-004", "<project>:k:pattern:lifecycle-token-tokenservice"],
  "architectural_decision": "Reuse existing token infrastructure for invitations.",
  "risks": ["Invitation lifecycle overlaps with the verification-token model."],
  "assumptions": ["Invites expire after 7 days."],
  "open_questions": []
}
```

The same plan rendered for humans (what `get_feature` and `plan.md` show):

```text
FEATURE PLAN
Feature: Team Invitations
Existing Architecture:
- TeamController -> TeamService -> TeamRepository -> teams, team_members
Reuse decisions:
- InvitationTokenService: reuse -> TokenService (covers the whole lifecycle)
Potential Files:
- src/services/team.service.ts ...
New Abstractions: none
Architectural Decision: Reuse existing token infrastructure.
Risks: Invitation lifecycle overlaps with the verification-token model.
```
