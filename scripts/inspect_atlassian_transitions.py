"""POINT 20: diagnose + apply the Atlassian hosted-MCP 'Done' transition for one issue.

Discovers the exact transition list + the arg shape `transitionJiraIssue` expects (the
hosted tool's schema isn't documented for us), then transitions the issue to Done.

Usage:  python scripts\\inspect_atlassian_transitions.py [ISSUE_KEY]   (default KAN-9)
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make `app` importable when run as `python scripts\...` without PYTHONPATH set.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml

import app.secrets as secrets
secrets.load_secrets()

from fastmcp import Client
from app.integrations.jira_mcp_client import _atlassian_oauth


def _find(d, key):
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def _data(r):
    d = getattr(r, "data", None)
    return d if d is not None else getattr(r, "structured_content", None)


def _dump_result(r):
    """Print every place an MCP result might carry its payload."""
    print("  .data             =", repr(getattr(r, "data", None))[:400])
    print("  .structured_content=", repr(getattr(r, "structured_content", None))[:400])
    content = getattr(r, "content", None)
    print("  .content type      =", type(content).__name__)
    if isinstance(content, list):
        for i, block in enumerate(content):
            txt = getattr(block, "text", None)
            print(f"  .content[{i}] {type(block).__name__}: {txt!r}"[:600])
    print("  .is_error          =", getattr(r, "is_error", None))


cfg = yaml.safe_load(Path("config/modules.yaml").read_text(encoding="utf-8"))
URL = _find(cfg, "jira_atlassian_url") or "https://mcp.atlassian.com/v1/mcp"
CLOUD = _find(cfg, "jira_cloud_id") or ""
KEY = sys.argv[1] if len(sys.argv) > 1 else "KAN-9"


async def main():
    print(f"url={URL}\ncloudId={CLOUD}\nissue={KEY}\n")
    async with Client(URL, auth=_atlassian_oauth(URL)) as c:
        # 1) confirm the exact tool names the hosted server exposes
        tools = await c.list_tools()
        names = [t.name for t in tools]
        print("=== TOOLS (transition/get-related) ===")
        for n in names:
            if any(w in n.lower() for w in ("transition", "gettransitions", "getjira")):
                print("  ", n)

        # 2) call getTransitions and dump the FULL raw result
        raw = await c.call_tool(
            "getTransitionsForJiraIssue", {"cloudId": CLOUD, "issueIdOrKey": KEY})
        print("\n=== getTransitionsForJiraIssue RAW RESULT ===")
        _dump_result(raw)

        # 3) parse transitions out of whichever field carried them
        tr = _data(raw)
        if tr is None:
            content = getattr(raw, "content", None) or []
            for block in content:
                txt = getattr(block, "text", None)
                if txt:
                    try:
                        tr = json.loads(txt)
                        break
                    except Exception:
                        pass
        print("\n=== parsed transitions ===")
        print(json.dumps(tr, indent=2, default=str)[:1500])

        transitions = tr.get("transitions", tr) if isinstance(tr, dict) else (tr or [])
        done = next(
            (t for t in transitions
             if "done" in (t.get("name") or (t.get("to") or {}).get("name") or "").lower()),
            None,
        )
        print("\n=== picked Done transition ===")
        print(json.dumps(done, indent=2, default=str))
        if not done:
            print("\nNo 'Done' transition found — paste the RAW list above and I'll map it.")
            return

        # Try several arg shapes for `transition` and report which one works.
        candidates = [
            ("id-string", done.get("id")),
            ("id-object", {"id": done.get("id")}),
            ("name-string", done.get("name")),
        ]
        for label, shape in candidates:
            if shape is None:
                continue
            try:
                res = _data(await c.call_tool(
                    "transitionJiraIssue",
                    {"cloudId": CLOUD, "issueIdOrKey": KEY, "transition": shape}))
                print(f"\n*** SUCCESS with transition={label} ({shape!r}) -> {res}")
                return
            except Exception as e:
                print(f"\n--- failed transition={label} ({shape!r}): {e}")
        print("\nAll shapes failed — paste the output and I'll adjust the tool args.")


asyncio.run(main())
