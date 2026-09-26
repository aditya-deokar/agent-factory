# S1: Vector and full-text indexes

Script: `code/s1_indexes.py` against `neo4j:5.26-community` (5.26.31).

- `CREATE VECTOR INDEX ... OPTIONS {indexConfig: {`vector.dimensions`: N, `vector.similarity_function`: 'cosine'}}` works.
- `db.index.vector.queryNodes(index, k, vector)` returns `node, score`; `db.index.fulltext.queryNodes(index, text)` works.
- The standard Lucene analyzer does **not** split camelCase: `token` matched `VerificationTokenService`
  only through a separate `name_tokens` property ("verification token service").
- `EXPLAIN <query>` in a READ routing call reports `summary.query_type` = `r` for reads, `w` for writes.

**Decision:** keep the Phase 2 index plan. Store a `name_tokens` property (camelCase split) on every Symbol and
index it in full-text. Phase 5's Text2Cypher guard uses `EXPLAIN` → `query_type == 'r'`.
