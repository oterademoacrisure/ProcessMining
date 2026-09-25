"""POINT 20 (Task #20): ONE-TIME OAuth authorization for the hosted Atlassian MCP.

Run this ONCE in your own terminal — a browser opens for Atlassian login + consent.
The OAuth token is saved in the OS keyring (service 'serverops-jira-mcp'), so the
unattended runner can then reuse + auto-refresh it (no browser per call) when
config has  jira_mcp_server: atlassian.

    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\authorize_atlassian.py
    ... <endpoint-url>     # override the default endpoint
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DEFAULT_URL = "https://mcp.atlassian.com/v1/mcp"   # /v1/sse deprecated after 2026-06-30


async def main(url: str) -> None:
    from app.integrations.jira_mcp_client import _atlassian_oauth
    from fastmcp import Client

    print(f"Authorizing against {url}")
    print("A browser will open for Atlassian login + consent...\n")
    async with Client(url, auth=_atlassian_oauth(url)) as client:
        tools = await client.list_tools()
        print(f"Authorized OK — {len(tools)} tools available.")
        print("Token saved to the OS keyring (service 'serverops-jira-mcp').")
        print("The runner can now use the hosted MCP unattended (set jira_mcp_server: atlassian).")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL))
