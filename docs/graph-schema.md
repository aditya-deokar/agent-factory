# Agent Factory graph schema

Schema version **2** · counts for project `teamapp-cli`

## Node labels

| Label | Count | Notes |
|---|---|---|
| `Project` | 0 | one per configured project |
| `Module` | 11 | package boundary (package.json, __init__.py, top-level source dir) |
| `File` | 28 | source file; `:TestFile` for tests |
| `TestFile` | 3 |  |
| `Symbol` | 92 | class / function / method / route / model; role labels added |
| `Knowledge` | 27 | super-label of Pattern, Decision, Constraint |
| `Pattern` | 11 | repeated implementation pattern |
| `Decision` | 4 | ADR or inferred architectural decision |
| `Constraint` | 12 | rule future code must not violate |
| `Feature` | 0 | feature memory: what a feature changed, reused, proved |
| `Evidence` | 88 | what backs a claim: code refs, test runs, screenshots |
| `Commit` | 25 | git history (author hashed) |
| `Doc` | 8 | README / ADR / guide |
| `DocChunk` | 8 | embedded documentation chunk |
| `AuditRun` | 1 | one audit execution |
| `MemoryEvent` | 29 | audit log entry for a knowledge change |
| `SchemaVersion` | 0 | applied migrations |

## Role labels (on `:Symbol`)

| Role | Count |
|---|---|
| `Service` | 6 |
| `Repository` | 3 |
| `Controller` | 3 |
| `Route` | 10 |
| `Component` | 0 |
| `Hook` | 0 |
| `Worker` | 0 |
| `Queue` | 0 |
| `Middleware` | 2 |
| `Model` | 4 |
| `Validator` | 6 |
| `Integration` | 1 |

## Relationships

| Type | Count |
|---|---|
| `CONTAINS` | 28 |
| `DEFINES` | 92 |
| `HAS_MEMBER` | 38 |
| `IMPORTS` | 63 |
| `DEPENDS_ON` | 18 |
| `USES` | 64 |
| `CALLS` | 49 |
| `EXTENDS` | 3 |
| `IMPLEMENTS` | 0 |
| `ACCESSES` | 12 |
| `HANDLES` | 10 |
| `COVERS` | 9 |
| `FOLLOWS` | 38 |
| `ESTABLISHES` | 10 |
| `CONSTRAINED_BY` | 18 |
| `SUPERSEDES` | 1 |
| `SUPPORTED_BY` | 85 |
| `CONTRADICTED_BY` | 3 |
| `REFERENCES` | 174 |
| `TOUCHES` | 46 |
| `CO_CHANGES` | 2 |
| `MODIFIES` | 0 |
| `INTRODUCES` | 0 |
| `REUSES` | 0 |
| `TESTED_BY` | 0 |
| `IMPLEMENTED_IN` | 0 |
| `HAS_EVIDENCE` | 0 |
| `FOLLOWED` | 0 |
| `CREATED` | 0 |
| `FROM_DOC` | 8 |
| `NEXT_CHUNK` | 0 |
| `DESCRIBES` | 12 |

## Indexes

| Name | Type | Labels | Properties | State |
|---|---|---|---|---|
| `af_chunk_embedding` | VECTOR (1536-d) | DocChunk | embedding | ONLINE |
| `af_chunk_uid` | RANGE | DocChunk | uid | ONLINE |
| `af_commit_uid` | RANGE | Commit | uid | ONLINE |
| `af_doc_uid` | RANGE | Doc | uid | ONLINE |
| `af_event_target` | RANGE | MemoryEvent | target_uid | ONLINE |
| `af_event_uid` | RANGE | MemoryEvent | uid | ONLINE |
| `af_evidence_uid` | RANGE | Evidence | uid | ONLINE |
| `af_feature_project` | RANGE | Feature | project_id, status | ONLINE |
| `af_feature_uid` | RANGE | Feature | uid | ONLINE |
| `af_file_path` | RANGE | File | project_id, path | ONLINE |
| `af_file_run` | RANGE | File | project_id, last_audit_run | ONLINE |
| `af_file_uid` | RANGE | File | uid | ONLINE |
| `af_knowledge_embedding` | VECTOR (1536-d) | Knowledge | embedding | ONLINE |
| `af_knowledge_status` | RANGE | Knowledge | project_id, status | ONLINE |
| `af_knowledge_text` | FULLTEXT | Knowledge | title, claim | ONLINE |
| `af_knowledge_uid` | RANGE | Knowledge | uid | ONLINE |
| `af_module_uid` | RANGE | Module | uid | ONLINE |
| `af_project_id` | RANGE | Project | id | ONLINE |
| `af_run_uid` | RANGE | AuditRun | uid | ONLINE |
| `af_schema_version_id` | RANGE | SchemaVersion | id | ONLINE |
| `af_symbol_embedding` | VECTOR (1536-d) | Symbol | embedding | ONLINE |
| `af_symbol_name` | RANGE | Symbol | name | ONLINE |
| `af_symbol_project` | RANGE | Symbol | project_id | ONLINE |
| `af_symbol_text` | FULLTEXT | Symbol | name, name_tokens, method_names, doc | ONLINE |
| `af_symbol_uid` | RANGE | Symbol | uid | ONLINE |

## Constraints

- `af_chunk_uid`
- `af_commit_uid`
- `af_doc_uid`
- `af_event_uid`
- `af_evidence_uid`
- `af_feature_uid`
- `af_file_uid`
- `af_knowledge_uid`
- `af_module_uid`
- `af_project_id`
- `af_run_uid`
- `af_schema_version_id`
- `af_symbol_uid`
