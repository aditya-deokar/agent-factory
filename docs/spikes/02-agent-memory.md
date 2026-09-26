# S2: neo4j-agent-memory 0.6.0

Script: `code/s2_agent_memory.py [--offline]` (local Neo4j only; it asserts `localhost`).

- API confirmed: `short_term.add_message/search_messages/get_conversation`,
  `long_term.add_fact/add_preference/search_entities/search_preferences/get_entity_by_name/add_relationship/add_entity`,
  `reasoning.start_trace/add_step/record_tool_call/complete_trace/get_similar_traces`, `client.get_context`.
  Search methods default to `threshold=0.7`.
- **Packaging bug:** the bolt connection path imports `nams`, which needs `httpx`, but it is not a declared base dependency.
  Fix: install `neo4j-agent-memory[openai,nams]`.
- `MemoryClient(settings, embedder=...)` accepts a custom `BaseEmbedder` (async `embed`, `dimensions`), which enables offline tests.
- `neo4j_agent_memory.mcp` exists (`Neo4jMemoryMCPServer`, `create_mcp_server`); its extra requires `fastmcp>=4`.
- Labels created: Conversation, Message, Entity, Preference, Fact, ReasoningTrace, ReasoningStep, Tool, ToolCall, User,
  ConsolidationRun, MemoryReadAudit (+ 12 constraints, vector indexes `*_embedding_idx`). **No overlap** with the domain-graph labels.
- Offline round trip (message → trace/step/tool call → fact → preference → search → similar traces → `get_context`): 2.8 s.
- The online run failed with 403 `inactive`: the GraphAcademy proxy session was idle (see S6).

**Decision:** depend on `neo4j-agent-memory[openai,nams]`. Only `memory/agent_memory.py` imports it.
Tests inject a hash embedder. Don't install their `mcp` extra; our own MCP server exposes memory tools (Phase 6).
Never run write spikes or tests against the shared workshop memory instance.
