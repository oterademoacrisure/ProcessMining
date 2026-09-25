"""POINT 21 (Task #21): read the pipeline_event table — the feed behind the live
"Live Activity" timeline. Prints per-incident timelines + the global system feed.

    python scripts\\inspect_pipeline_events.py                 # tenant 1, last 200
    python scripts\\inspect_pipeline_events.py --tenant 1 --limit 300
    python scripts\\inspect_pipeline_events.py --corr rca-t1-mule-...   # one incident
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.db.session import SessionLocal

ap = argparse.ArgumentParser(description="Inspect pipeline_event rows")
ap.add_argument("--tenant", type=int, default=1)
ap.add_argument("--limit", type=int, default=200)
ap.add_argument("--corr", default=None, help="filter to one correlation_id")
args = ap.parse_args()

ICON = {"started": "...", "progress": " ->", "succeeded": " OK", "failed": "XXX", "skipped": "  -"}

sql = ("SELECT event_id, created_at, correlation_id, run_id, stage, step, status, "
       "level, message, meta FROM pipeline_event WHERE tenant_id = :t ")
params = {"t": args.tenant}
if args.corr:
    sql += "AND correlation_id = :c "
    params["c"] = args.corr
sql += "ORDER BY event_id ASC LIMIT :lim"
params["lim"] = args.limit

with SessionLocal() as s:
    rows = list(s.execute(text(sql), params))

if not rows:
    print(f"(no pipeline_event rows for tenant {args.tenant})")
    raise SystemExit(0)

# split per-incident vs global
incidents: dict[str, list] = {}
glob: list = []
for r in rows:
    (incidents.setdefault(r.correlation_id, []) if r.correlation_id else glob).append(r)


def _fmt(r) -> str:
    ts = r.created_at.strftime("%H:%M:%S")
    meta = r.meta if isinstance(r.meta, dict) else {}
    chips = "  ".join(f"{k}={v}" for k, v in meta.items()) if meta else ""
    body = r.step or r.message or ""
    return f"  {ICON.get(r.status,'  ?')} {ts}  {r.stage:13} {body}   {chips}".rstrip()


print(f"\n=== {len(incidents)} incident timeline(s) — tenant {args.tenant} ===")
for corr, evs in incidents.items():
    print(f"\n* {corr}   ({len(evs)} events)")
    for r in evs:
        print(_fmt(r))

if glob:
    print(f"\n=== system activity ({len(glob)} events) ===")
    for r in glob:
        print(_fmt(r))

print(f"\n(total rows: {len(rows)})")
