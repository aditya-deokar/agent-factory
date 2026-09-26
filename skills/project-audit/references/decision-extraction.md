# Decision extraction

## ADRs

ADRs in `docs/adr/` (MADR or Nygard format) become Decisions automatically: Accepted → validated,
Proposed → candidate, Superseded → superseded with a SUPERSEDES link. Check that:

- each ADR's decision text is what the code actually does (open the evidence behind the constraints it
  ESTABLISHES);
- superseded ADRs are not still followed somewhere (`agent-factory impact <symbol>` on what they govern).

## Decisions without ADRs

Look for decisions the team made but never wrote down:

- `git log --oneline --grep=refactor` and `--grep=revert`: moves of logic between layers, reverted
  approaches;
- configuration that constrains design (one ORM, one queue, one validation library);
- the same problem solved the same way in three places.

Propose each as `--kind decision` with the commits or files as evidence. Suggest an ADR to the
developer for anything important: an ADR becomes validated knowledge on the next audit.
