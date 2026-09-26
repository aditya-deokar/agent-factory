# Phase 0 spikes

Timeboxed checks run before building on each library. Scripts live in `code/`.
Every note ends in a **Decision**. Run date: 2026-09-26, Windows 11, Python 3.12.4.

| # | Question | Result |
|---|---|---|
| [S1](01-indexes.md) | Vector + full-text indexes, EXPLAIN query type | ✅ |
| [S2](02-agent-memory.md) | neo4j-agent-memory 0.6.0 API and labels | ✅ (offline embedder) |
| [S3](03-graphrag.md) | VectorCypherRetriever with custom index/query/params | ✅ |
| [S4](04-mcp.md) | MCP server over stdio | ✅ (mcp 2.x API change) |
| [S5](05-tree-sitter.md) | tree-sitter TS/TSX/JS/Python on Windows | ✅ |
| [S6](06-embeddings.md) | Embedding latency through the GraphAcademy proxy | ⏸ blocked: proxy session idle |

Pinned versions (see `uv.lock`): neo4j 6.3.1 · neo4j-graphrag 1.21.0 · neo4j-agent-memory 0.6.0
(`[openai,nams]`) · mcp 2.2.0 · tree-sitter 0.26.0 · tree-sitter-language-pack 1.20.0 · Neo4j server 5.26.31.
