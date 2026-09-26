# Architecture map

What to confirm after an audit, and how.

## Layers

The auditor gives each class or function one role: Route, Controller, Service, Repository, Model,
Validator, Middleware, Integration, Component, Hook, Worker, Queue. Check a sample of each role:

- Is every `Service` really business logic, or are some thin wrappers that belong in `Integration`?
- Do `Repository` classes own all table access? `agent-factory memory show data-access-through-repositories`.
- Are there classes with no role that are important? They usually lack a naming convention. Propose one.

## Flows

`agent-factory context "<a typical request>"` prints flows such as
`TeamController → TeamService → TeamRepository → teams`. Confirm that the main flows match how the
team describes the system. A missing hop usually means a dependency the auditor could not resolve
(dynamic imports, dependency-injection containers): say so in a candidate note.

## Modules

`agent-factory ask "Which modules depend on which?"` (or the DEPENDS_ON edges in Neo4j Browser) shows
module coupling. Cycles between modules are worth a candidate constraint with evidence.
