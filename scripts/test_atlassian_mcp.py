"""POINT 20 (Task #20): connect to Atlassian's HOSTED MCP server and list its tools.

Run this in YOUR OWN terminal — a browser window will open for Atlassian login +
consent (OAuth). After you authorize, it prints the tools the hosted server exposes
(and the input schema of any "create issue" tool), so we can decide whether to wire
serverops to it directly.

Usage:
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\test_atlassian_mcp.py
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\test_atlassian_mcp.py <endpoint-url>

Note: confirm the current endpoint in Atlassian's docs ("Atlassian Remote MCP Server").
"""
from __future__ import annotations

import asyncio
import json
import sys

# Default Atlassian Remote MCP endpoint (verify in Atlassian docs; pass your own as argv[1]).
DEFAULT_URL = "https://mcp.atlassian.com/v1/mcp"   # /v1/sse deprecated after 2026-06-30


async def main(url: str) -> None:
    from fastmcp import Client

    print(f"Connecting to Atlassian hosted MCP: {url}")
    print("A browser will open for Atlassian login + consent (OAuth)...\n")

    async with Client(url, auth="oauth") as client:
        tools = await client.list_tools()
        print(f"Connected. {len(tools)} tool(s) exposed:\n")
        for t in tools:
            print(f"  - {t.name}: {(t.description or '')[:90]}")

        # Show the schema of any create-issue-like tool (so we know its args).
        for t in tools:
            n = t.name.lower()
            if "issue" in n and ("creat" in n or "add" in n):
                print(f"\ninput schema for {t.name}:")
                print(json.dumps(getattr(t, "inputSchema", {}), indent=2)[:1500])


if __name__ == "__main__":
    endpoint = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    asyncio.run(main(endpoint))
