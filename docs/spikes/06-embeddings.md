# S6: Embeddings through the GraphAcademy proxy

**Status: blocked.** The proxy answered `403 inactive`: "Your workshop AI session has gone idle. Open any lesson of the
course in your browser (or call any GraphAcademy MCP tool) to reactivate."

To finish: reactivate the session, then run `uv run python docs/spikes/code/s2_agent_memory.py` (online mode)
and time a 500-card batch.

**Decision (provisional):** the audit supports `--no-embed` and caches embeddings by content hash. Tests never call the
network (hash or recorded embeddings), so the proxy is needed only for live runs.
