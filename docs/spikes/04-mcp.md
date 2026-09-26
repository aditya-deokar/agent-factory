# S4: MCP server (mcp 2.2.0)

Script: `code/s4_mcp_server.py [--client]`.

- **API change:** mcp 2.x renamed `FastMCP` to `MCPServer` (`from mcp.server.mcpserver import MCPServer`).
  `@server.tool()` and `server.run("stdio")` work the same way.
- `mcp.client.client.Client` accepts either the `MCPServer` object (in-memory, ideal for contract tests) or
  `StdioServerParameters` (real subprocess). Both returned the tool result.
- Tool results come back as `TextContent` JSON; dict returns are serialized automatically.

**Decision:** Phase 6 uses `MCPServer`. Contract tests use `Client(server)` in memory, and one smoke test uses stdio.
Registering in Claude Code/VS Code is a manual acceptance step in Phase 6 (`claude mcp add af-spike -- uv run python docs/spikes/code/s4_mcp_server.py`).
