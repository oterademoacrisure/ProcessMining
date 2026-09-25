"""POINT 20 (Task #20): FastMCP server exposing Jira issue creation as an MCP tool.

Runs locally as a standalone process (stdio transport) — NO Docker. serverops' MCP
client spawns it and calls `create_issue`.

  - If JIRA_URL + JIRA_EMAIL + JIRA_API_TOKEN are set -> POSTs to the Jira REST API
    (v2 — plain-text description, works on Cloud + Data Center) and returns the real key.
  - Otherwise -> MOCK: writes the ticket payload to a local file and returns a synthetic
    key, so the whole MCP flow works end-to-end without any credentials.

Run standalone (for testing / reuse by other MCP clients):
    serverops\\venv\\Scripts\\python.exe app\\mcp_servers\\jira_server.py
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastmcp import FastMCP

try:
    from app.secrets import load_secrets
    load_secrets()                 # .env locally; Azure Key Vault in the cloud
except Exception:
    from dotenv import load_dotenv  # fallback if run outside the app package
    load_dotenv()

mcp = FastMCP("jira")

_MOCK_DIR = Path(os.getenv("JIRA_MOCK_DIR", "data/output/jira_mock"))


def _creds() -> tuple[str, str, str] | None:
    url = (os.getenv("JIRA_URL", "") or "").rstrip("/")
    email = os.getenv("JIRA_EMAIL", "")
    token = os.getenv("JIRA_API_TOKEN", "")
    return (url, email, token) if (url and email and token) else None


@mcp.tool
def create_issue(
    summary: str,
    description: str,
    project: str,
    issuetype: str = "Task",
    labels: list[str] | None = None,
) -> dict:
    """Create a Jira issue. Returns {"key", "url", "mode"} (mode = real | mock)."""
    labels = labels or []
    creds = _creds()

    if creds is None:
        # No credentials -> mock: write the payload locally so the MCP flow is exercised.
        _MOCK_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        rec = {
            "summary": summary, "description": description, "project": project,
            "issuetype": issuetype, "labels": labels, "_mock_created_at": stamp,
        }
        path = _MOCK_DIR / f"jira-{stamp}.json"
        path.write_text(json.dumps(rec, indent=2), encoding="utf-8")
        return {"key": f"MOCK-{stamp}", "url": path.resolve().as_uri(), "mode": "mock"}

    url, email, token = creds
    payload = {
        "fields": {
            "project": {"key": project},
            "summary": summary[:250],
            "description": description,
            "issuetype": {"name": issuetype},
            "labels": labels,
        }
    }
    resp = httpx.post(
        f"{url}/rest/api/2/issue", json=payload, auth=(email, token),
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"key": data["key"], "url": f"{url}/browse/{data['key']}", "mode": "real"}


@mcp.tool
def transition_issue(key: str, status: str = "Done") -> dict:
    """Move a Jira issue to a status (by transition/target name, e.g. 'Done')."""
    creds = _creds()
    if creds is None:
        return {"key": key, "status": status, "mode": "mock"}   # no creds -> no-op
    url, email, token = creds
    auth, headers = (email, token), {"Accept": "application/json", "Content-Type": "application/json"}
    g = httpx.get(f"{url}/rest/api/2/issue/{key}/transitions", auth=auth, headers=headers, timeout=20)
    g.raise_for_status()
    transitions = g.json().get("transitions", [])
    target = status.lower()
    match = next(
        (t for t in transitions
         if (t.get("to") or {}).get("name", "").lower() == target or t.get("name", "").lower() == target),
        None,
    )
    if not match:
        return {"key": key, "status": "unchanged", "available": [t.get("name") for t in transitions]}
    p = httpx.post(f"{url}/rest/api/2/issue/{key}/transitions",
                   json={"transition": {"id": match["id"]}}, auth=auth, headers=headers, timeout=20)
    p.raise_for_status()
    return {"key": key, "status": status, "mode": "real"}


if __name__ == "__main__":
    # Two run modes:
    #   (default) stdio  — the client spawns this script as a subprocess
    #   --http / JIRA_MCP_TRANSPORT=http — run as a standalone HTTP web service
    #         (e.g. http://127.0.0.1:8090/mcp), like a FastAPI app, that any MCP client connects to
    import argparse
    ap = argparse.ArgumentParser(description="Jira MCP server")
    ap.add_argument("--http", action="store_true", help="run as an HTTP web service (not stdio)")
    ap.add_argument("--host", default=os.getenv("JIRA_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.getenv("JIRA_MCP_PORT", "8090")))
    args = ap.parse_args()
    if args.http or os.getenv("JIRA_MCP_TRANSPORT", "").lower() == "http":
        mcp.run(transport="http", host=args.host, port=args.port, show_banner=False)
    else:
        mcp.run(show_banner=False)   # stdio
