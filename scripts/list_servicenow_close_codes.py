"""POINT 19: list the valid incident 'Resolution code' (close_code) values on the
configured ServiceNow instance, so resolve-on-verify sends an accepted value.

Usage: serverops\\venv\\Scripts\\python.exe serverops\\scripts\\list_servicenow_close_codes.py
"""
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

url = (os.getenv("SERVICENOW_INSTANCE_URL", "") or "").rstrip("/")
user = os.getenv("SERVICENOW_USERNAME", "")
pwd = os.getenv("SERVICENOW_PASSWORD", "")
if not (url and user and pwd):
    print("SERVICENOW_* env vars not set"); sys.exit(1)

resp = httpx.get(
    f"{url}/api/now/table/sys_choice",
    params={
        "sysparm_query": "name=incident^element=close_code^inactive=false",
        "sysparm_fields": "label,value,sequence",
        "sysparm_limit": "100",
    },
    auth=(user, pwd),
    headers={"Accept": "application/json"},
    timeout=30,
)
rows = resp.json().get("result", [])
print(f"Valid incident close_code values on {url} ({len(rows)}):\n")
for c in sorted(rows, key=lambda r: r.get("sequence") or ""):
    print(f"  value={c.get('value')!r:<40} label={c.get('label')!r}")
print("\nPick a `value` above and set it as resolve_close_code in config/modules.yaml.")
