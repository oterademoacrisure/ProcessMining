"""POINT 21 (Task #21): emit a synthetic incident timeline into pipeline_event,
so you can see the Streamlit "Live Activity" page render without running the full
pipeline. Validates the table + ProgressEmitter end-to-end.

Run the migration first (creates the table):
    python -m alembic upgrade head

Then:
    python scripts\\smoke_progress.py [--tenant 1]
    # open Streamlit -> Live Activity (same tenant)
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.observability import progress

ap = argparse.ArgumentParser(description="Emit a synthetic pipeline timeline")
ap.add_argument("--tenant", type=int, default=1)
args = ap.parse_args()
T = args.tenant
CORR = f"rca-t{T}-smoke-demo-critical"

STEPS = [
    ("DIAGNOSE",     "started",   "RCA analysis", None),
    ("DIAGNOSE",     "progress",  "retrieving precedents", None),
    ("DIAGNOSE",     "progress",  "found 3 precedent(s)", {"precedents": 3}),
    ("DIAGNOSE",     "progress",  "calling Azure OpenAI (RCA synthesis)", None),
    ("DIAGNOSE",     "succeeded", "RCA analysis", {"severity": "critical", "precedents": 3}),
    ("SNOW_CREATE",  "started",   "ServiceNow ticket", None),
    ("SNOW_CREATE",  "succeeded", "ServiceNow ticket", {"inc": "INC0099999", "mode": "mock"}),
    ("APPROVAL",     "succeeded", "human approved", None),
    ("VALIDATE",     "succeeded", "allow-list check",
     {"allowed": True, "command": "kubectl rollout restart deployment/appian-webapp"}),
    ("EXECUTE",      "started",   "run fix", None),
    ("EXECUTE",      "succeeded", "run fix", {"exit_code": 0, "duration_sec": 1.5}),
    ("VERIFY",       "started",   "verify fix", None),
    ("VERIFY",       "succeeded", "verify fix", {"passed": True}),
    ("JIRA_CREATE",  "succeeded", "Jira audit ticket", {"jira_key": "KAN-99", "status": "Done"}),
    ("SNOW_RESOLVE", "succeeded", "resolve ServiceNow", {"jira_key": "KAN-99"}),
    ("LEARN",        "succeeded", "capture precedent", {"outcome": "approved"}),
]

print(f"Emitting {len(STEPS)} events for tenant {T}, correlation_id={CORR}\n")
for stage, status, step, meta in STEPS:
    progress.emit(stage, status, tenant_id=T, correlation_id=CORR, step=step, meta=meta)
    print(f"  {stage:12} {status:9} {step}")
    time.sleep(0.4)

# a couple of global "system activity" feed rows (no correlation_id)
progress.emit("INGEST", "succeeded", tenant_id=T, run_id="smoke",
              message="12 event(s) ingested", meta={"count": 12})
progress.emit("DETECT", "succeeded", tenant_id=T, run_id="smoke",
              message="3 finding(s) detected", meta={"count": 3})

print(f"\nDone. Open Streamlit -> 'Live Activity' (Tenant ID = {T}).")
