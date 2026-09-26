# Relationship mapping

The relationships the auditor writes, and how to use them.

| Relationship | Meaning | Ask it |
|---|---|---|
| `USES` | class-level dependency (constructor injection, `new`, calls) | "What does TeamService depend on?" |
| `CALLS` | method-level call with a known receiver type | `agent-factory impact TokenService` |
| `ACCESSES {op}` | code reading or writing a table | "Which tables does TokenRepository access?" |
| `HANDLES` | an HTTP route and the method that handles it | "Which routes reach TokenService?" |
| `COVERS` | a test file importing a symbol | the "Tests to run" list of `impact` |
| `CO_CHANGES {count}` | files changed together in 3+ commits | hidden coupling not visible in imports |
| `FOLLOWS` / `CONSTRAINED_BY` | symbols that follow a pattern / fall under a rule | `get_constraints` with a scope |
| `ESTABLISHES` / `SUPERSEDES` | a decision behind a rule / a decision replaced by another | `memory show <id>` |

Structured questions go to `agent-factory ask "..."` (read-only Text2Cypher). The saved query below
shows the architecture of one feature in Neo4j Browser:

```cypher
MATCH p = (:Symbol:Route)-[:HANDLES]->()<-[:HAS_MEMBER]-(:Symbol:Controller)-[:USES]->(:Symbol:Service)
          -[:USES]->(:Symbol:Repository)-[:HAS_MEMBER]->()-[:ACCESSES]->(:Symbol:Model)
RETURN p LIMIT 25
```
