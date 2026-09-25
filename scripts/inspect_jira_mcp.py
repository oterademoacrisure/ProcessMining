"""POINT 20: list the tools (names, descriptions, input schemas) exposed by an MCP server.

Defaults to our own Jira MCP web service. The server must be running:
    python app\\mcp_servers\\jira_server.py --http

Usage:
    python scripts\\inspect_jira_mcp.py                       # http://127.0.0.1:8090/mcp
    python scripts\\inspect_jira_mcp.py http://127.0.0.1:8090/mcp
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastmcp import Client

TARGET = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8090/mcp"


async def main():
    print(f"Connecting to {TARGET}\n")
    async with Client(TARGET) as c:
        tools = await c.list_tools()
        print(f"{len(tools)} tool(s):\n")
        for t in tools:
            print(f"- {t.name}")
            if t.description:
                print(f"    {t.description.strip().splitlines()[0]}")
            schema = getattr(t, "inputSchema", None) or {}
            props = schema.get("properties", {})
            required = set(schema.get("required", []))
            for name, spec in props.items():
                req = " (required)" if name in required else ""
                typ = spec.get("type", "?")
                dflt = f", default={spec['default']!r}" if "default" in spec else ""
                print(f"      • {name}: {typ}{req}{dflt}")
            print()


asyncio.run(main())
