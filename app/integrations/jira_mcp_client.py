"""POINT 20 (Task #20): MCP client for filing Jira tickets — switchable between our
OWN local FastMCP server (token auth) and Atlassian's HOSTED MCP server (OAuth).

Config (remediation block):
  jira_mcp_server: own       -> app/mcp_servers/jira_server.py, tool `create_issue`
                                (stdio default, or http via jira_mcp_url)
  jira_mcp_server: atlassian -> hosted Atlassian MCP (jira_atlassian_url), tool
                                `createJiraIssue`, OAuth.

OAuth tokens for the hosted server are persisted in the OS keyring, so after a ONE-TIME
browser authorization (scripts/authorize_atlassian.py) the unattended runner reuses +
auto-refreshes them — no browser per call. Fail-safe: returns None on any error.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

log = logging.getLogger(__name__)

_OWN_SERVER = str(Path(__file__).resolve().parents[1] / "mcp_servers" / "jira_server.py")
# POINT 20/21: keyring namespace for the hosted-MCP OAuth token. Bumped to force a
# clean slate after the /v1/sse -> /v1/mcp endpoint change (stale token from the old
# endpoint caused "token refresh failed"). Bump again if you ever need a fresh login.
_KEYRING_SERVICE = "serverops-jira-mcp-mcpv1"


def _atlassian_oauth(url: str):
    """FastMCP OAuth with persistent (OS keyring) token storage."""
    from fastmcp.client.auth import OAuth
    from key_value.aio.stores.keyring import KeyringStore
    return OAuth(mcp_url=url, token_storage=KeyringStore(service_name=_KEYRING_SERVICE))


def _data(result):
    """Extract a tool result's payload. Our own server returns it in `.data`; the
    Atlassian hosted server returns JSON inside text content blocks (often preceded by
    a deprecation-notice block), so fall back to parsing the first JSON-decodable block."""
    data = getattr(result, "data", None)
    if data is not None:
        return data
    data = getattr(result, "structured_content", None)
    if data is not None:
        return data
    import json
    for block in (getattr(result, "content", None) or []):
        txt = getattr(block, "text", None)
        if not txt:
            continue
        try:
            return json.loads(txt)
        except Exception:
            continue          # e.g. the plaintext deprecation notice — skip to the JSON block
    return None


async def _own_create(target, args: dict, mark_done: bool) -> dict | None:
    """Create via our server and (if mark_done) transition to Done — one MCP session."""
    from fastmcp import Client
    async with Client(target) as client:
        norm = _normalize(_data(await client.call_tool("create_issue", args)), "own")
        if mark_done and norm and norm.get("key"):
            try:
                await client.call_tool("transition_issue", {"key": norm["key"], "status": "Done"})
                norm["status"] = "Done"
            except Exception:
                log.exception("jira: transition to Done failed (own); ticket still created")
        return norm


async def _atlassian_create(url, cloud: str, args: dict, mark_done: bool) -> dict | None:
    """Create via Atlassian's hosted MCP and (best-effort) transition to Done."""
    from fastmcp import Client
    async with Client(url, auth=_atlassian_oauth(url)) as client:
        norm = _normalize(_data(await client.call_tool("createJiraIssue", args)), "atlassian")
        if mark_done and norm and norm.get("key"):
            try:
                td = _data(await client.call_tool(
                    "getTransitionsForJiraIssue", {"cloudId": cloud, "issueIdOrKey": norm["key"]}))
                transitions = td.get("transitions", []) if isinstance(td, dict) else (td or [])
                done = next(
                    (t for t in transitions
                     if "done" in (t.get("name") or (t.get("to") or {}).get("name") or "").lower()),
                    None,
                )
                if done and done.get("id"):
                    # transitionJiraIssue requires `transition` as an OBJECT: {"id": "<id>"}.
                    await client.call_tool("transitionJiraIssue", {
                        "cloudId": cloud, "issueIdOrKey": norm["key"],
                        "transition": {"id": done["id"]},
                    })
                    norm["status"] = "Done"
            except Exception:
                log.exception("jira: transition to Done failed (atlassian); ticket still created")
        return norm


def _normalize(data, server: str) -> dict | None:
    """Normalize either server's return into {key, url, mode}."""
    if data is None:
        return None
    if not isinstance(data, dict):
        return {"key": str(data), "url": None, "mode": server}
    if "key" in data and "mode" in data:        # our server already returns this shape
        return data
    key = (data.get("key") or (data.get("issue") or {}).get("key")
           or data.get("issueKey") or data.get("id"))
    url = data.get("url") or data.get("self")
    return {"key": key, "url": url, "mode": server}


def file_ticket(*, summary: str, description: str, project: str,
                issuetype: str = "Task", labels: list[str] | None = None,
                mark_done: bool = False, config: dict | None = None) -> dict | None:
    """File a Jira ticket via the configured MCP server. If mark_done, also transition
    it to Done (a verified fix = completed work). Returns {key,url,mode,status?} or None."""
    config = config or {}
    labels = labels or []
    server = str(config.get("jira_mcp_server", "own")).lower()
    # POINT 20/21: hard timeout so a slow/blocking MCP call (e.g. an un-authorized
    # hosted-OAuth flow waiting on a browser) can NEVER hang the runner. On timeout we
    # fail safe (no ticket) and the incident still resolves + learns.
    _timeout = float(config.get("jira_mcp_timeout_sec", 30))
    try:
        if server == "atlassian":
            url = config.get("jira_atlassian_url", "https://mcp.atlassian.com/v1/mcp")
            cloud = config.get("jira_cloud_id", "")
            args = {
                "cloudId": cloud,
                "projectKey": project,
                "issueTypeName": issuetype,
                "summary": summary,
                "description": description,
            }
            if labels:
                args["additional_fields"] = {"labels": labels}
            return asyncio.run(asyncio.wait_for(
                _atlassian_create(url, cloud, args, mark_done), _timeout))

        # own server: stdio (default) or http
        if str(config.get("jira_mcp_transport", "stdio")).lower() == "http":
            target = config.get("jira_mcp_url", "http://127.0.0.1:8090/mcp")
        else:
            target = _OWN_SERVER
        args = {"summary": summary, "description": description, "project": project,
                "issuetype": issuetype, "labels": labels}
        return asyncio.run(asyncio.wait_for(_own_create(target, args, mark_done), _timeout))
    except Exception:
        log.exception("file_ticket: failed (server=%s)", server)
        return None
