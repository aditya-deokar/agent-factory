# S3: neo4j-graphrag VectorCypherRetriever

Script: `code/s3_graphrag.py` (offline hash embedder implementing `neo4j_graphrag.embeddings.base.Embedder.embed_query`).

- `VectorCypherRetriever(driver, index_name, retrieval_query, embedder, neo4j_database=)` works with our own index names.
- `search(query_text, top_k, query_params={...}, filters=..., effective_search_ratio=...)`: `query_params` reach the retrieval query,
  so `WHERE node.project_id = $project_id` scoping works.
- Scoping filters **after** the vector top-k: `top_k=4` returned 3 rows when one hit belonged to another project.
- `Text2CypherRetriever(driver, llm, neo4j_schema=, examples=, custom_prompt=)` accepts a schema string and examples.

**Decision:** use `VectorCypherRetriever` in Phase 5. Oversample (`effective_search_ratio` or top_k × 3) when a DB holds several projects.
