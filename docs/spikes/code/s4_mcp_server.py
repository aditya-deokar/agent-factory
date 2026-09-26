"""Spike S4: minimal MCP server with the mcp 2.x API (FastMCP is now MCPServer).

Run as a stdio server:   uv run python docs/spikes/code/s4_mcp_server.py
Run the client check:    uv run python docs/spikes/code/s4_mcp_server.py --client
Register in Claude Code: claude mcp add af-spike -- uv run python docs/spikes/code/s4_mcp_server.py
"""

import sys

from mcp.server.mcpserver import MCPServer

server = MCPServer("agent-factory-spike")


@server.tool()
def find_reusable(name: str) -> dict:
    """Call BEFORE creating a new class or service. Finds existing implementations."""
    return {"proposed": name, "top": "TokenService", "verdict": "reuse"}


async def client_check() -> None:
    from mcp.client.client import Client
    from mcp.client.stdio import StdioServerParameters

    # In-memory: the server object itself is the transport (contract tests use this).
    async with Client(server) as c:
        tools = await c.list_tools()
        print("in-memory tools:", [t.name for t in tools.tools])
        res = await c.call_tool("find_reusable", {"name": "InvitationTokenService"})
        print("in-memory result:", res.structured_content or res.content)

    # Real subprocess over stdio (what Claude Code / Cursor do).
    params = StdioServerParameters(command=sys.executable, args=[__file__])
    async with Client(params) as c:
        res = await c.call_tool("find_reusable", {"name": "InvitationTokenService"})
        print("stdio result:", res.structured_content or res.content)


if __name__ == "__main__":
    if "--client" in sys.argv:
        import anyio

        anyio.run(client_check)
    else:
        server.run("stdio")
