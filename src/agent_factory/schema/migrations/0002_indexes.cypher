// Lookup, full-text and vector indexes. {{dims}} is the configured embedding size.
CREATE INDEX af_symbol_name IF NOT EXISTS FOR (n:Symbol) ON (n.name);
CREATE INDEX af_symbol_project IF NOT EXISTS FOR (n:Symbol) ON (n.project_id);
CREATE INDEX af_file_path IF NOT EXISTS FOR (n:File) ON (n.project_id, n.path);
CREATE INDEX af_file_run IF NOT EXISTS FOR (n:File) ON (n.project_id, n.last_audit_run);
CREATE INDEX af_knowledge_status IF NOT EXISTS FOR (n:Knowledge) ON (n.project_id, n.status);
CREATE INDEX af_feature_project IF NOT EXISTS FOR (n:Feature) ON (n.project_id, n.status);
CREATE INDEX af_event_target IF NOT EXISTS FOR (n:MemoryEvent) ON (n.target_uid);
CREATE FULLTEXT INDEX af_symbol_text IF NOT EXISTS FOR (n:Symbol) ON EACH [n.name, n.name_tokens, n.method_names, n.doc];
CREATE FULLTEXT INDEX af_knowledge_text IF NOT EXISTS FOR (n:Knowledge) ON EACH [n.title, n.claim];
CREATE VECTOR INDEX af_symbol_embedding IF NOT EXISTS FOR (n:Symbol) ON n.embedding
  OPTIONS {indexConfig: {`vector.dimensions`: {{dims}}, `vector.similarity_function`: 'cosine'}};
CREATE VECTOR INDEX af_chunk_embedding IF NOT EXISTS FOR (n:DocChunk) ON n.embedding
  OPTIONS {indexConfig: {`vector.dimensions`: {{dims}}, `vector.similarity_function`: 'cosine'}};
CREATE VECTOR INDEX af_knowledge_embedding IF NOT EXISTS FOR (n:Knowledge) ON n.embedding
  OPTIONS {indexConfig: {`vector.dimensions`: {{dims}}, `vector.similarity_function`: 'cosine'}};
