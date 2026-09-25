"""POINT 19: inspect the latest pipeline run (read-only).

Shows the "conveyor belt" table counts, each root_cause_report (summary +
incident sources), the exact PRECEDENT BLOCK the LLM received (ticket, outcome,
confidence, resolution), and the recommended actions per report.

Usage:
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\inspect_run.py [--tenant 1]
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import func, select

from app.db.models import (
    EventLog, Finding, HistoricalIncident, RemediationAction, RootCauseReport,
)
from app.db.session import SessionLocal


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect the latest pipeline run")
    ap.add_argument("--tenant", type=int, default=1)
    args = ap.parse_args()

    with SessionLocal() as s:
        print("=== TABLE COUNTS (the conveyor belt) ===")
        for label, model in [
            ("event_log", EventLog), ("finding", Finding),
            ("root_cause_report", RootCauseReport), ("remediation_action", RemediationAction),
            ("historical_incident (precedent corpus)", HistoricalIncident),
        ]:
            n = s.scalar(select(func.count()).select_from(model).where(model.tenant_id == args.tenant))
            print(f"  {label:<42} {n}")

        reports = list(s.scalars(
            select(RootCauseReport).where(RootCauseReport.tenant_id == args.tenant)
            .order_by(RootCauseReport.report_id)
        ))
        print(f"\n=== REPORTS ({len(reports)}) ===")
        for r in reports:
            p = r.payload or {}
            print(f"\nreport_id={r.report_id}  severity={r.severity}  sources={p.get('incident_sources')}")
            print(f"  servicenow: number={r.servicenow_number}  sys_id={r.servicenow_sys_id}")
            print(f"  summary: {(r.summary or '')[:300]}")

            precs = p.get("historical_precedents") or []
            print(f"  --- PRECEDENT BLOCK fed to the LLM ({len(precs)}) ---")
            for pr in precs:
                print(f"     [{(pr.get('outcome') or '?'):8}] conf={pr.get('confidence')}  "
                      f"{pr.get('ticket')}  {(pr.get('what') or '')[:55]}")
            if not precs:
                print("     (none cleared the 0.60 bar for this report)")

            acts = list(s.scalars(
                select(RemediationAction).where(RemediationAction.report_id == r.report_id)
            ))
            print(f"  --- RECOMMENDED ACTIONS ({len(acts)}) ---")
            for a in acts:
                cmd = f"   $ {a.command}" if a.command else ""
                print(f"     [{a.action_type:<10} {a.state:<8}] {(a.action_text or '')[:70]}{cmd}")


if __name__ == "__main__":
    main()
