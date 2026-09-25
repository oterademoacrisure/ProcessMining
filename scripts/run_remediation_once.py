"""POINT 19: run the remediation dispatcher ONCE (controlled demo).

Picks up APPROVED + EXECUTABLE actions and runs the LangGraph workflow
(validate -> execute -> verify -> report), including the Step-7 precedent
feedback + the ServiceNow resolve-on-verify. Normally this runs as a cycle
inside runner.py; this script runs exactly one cycle so you can observe it.

Usage:
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\run_remediation_once.py [--tenant 1]
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

import yaml
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.db.session import SessionLocal
from app.orchestrator.remediation_dispatcher import RemediationDispatcher
from app.paths import PROJECT_ROOT


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one remediation cycle (Task #19 demo)")
    ap.add_argument("--tenant", type=int, default=1)
    ap.add_argument("--log-level", default="INFO")
    args = ap.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
    )

    with open(PROJECT_ROOT / "config" / "modules.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # POINT 21 (Task #21): wire the live stage emitter (+ optional OTel/Langfuse).
    from app.observability import progress
    from app.observability.telemetry import init_telemetry
    progress.configure(cfg)
    init_telemetry(cfg)
    rem_cfg = cfg.get("remediation", {})
    print(f"executor_mode={rem_cfg.get('executor_mode')}  "
          f"verifier_mode={rem_cfg.get('verifier_mode')}  "
          f"precedent_feedback={rem_cfg.get('precedent_feedback')}")

    with SessionLocal() as s:
        n = RemediationDispatcher(rem_cfg, tenant_ids=[args.tenant]).run_once(s)
    print(f"\nremediation dispatcher processed {n} approved+executable action(s)")
    if n == 0:
        print("(none found — approve an EXECUTABLE action in the UI first, "
              "or this run produced only advisory actions)")


if __name__ == "__main__":
    main()
