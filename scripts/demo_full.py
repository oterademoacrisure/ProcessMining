"""One-shot, deterministic end-to-end demo of the FULL pipeline (all sources).

Runs the whole pipeline in a SINGLE pass so the cross-source correlation is
repeatable (no multi-cycle grouping nondeterminism):

    reset -> ingest all sources once -> one dispatch -> one investigate

By default it ingests ALL real sources (Appian x3, Mule, Prometheus, Fluentd)
so reports correlate across BPM + integration + infra pillars, enriched with
ServiceNow precedent. Prints what landed at each stage:

    event_log -> finding -> incident -> root_cause_report -> remediation_action

Usage (run from anywhere; resolves serverops paths itself):

    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\demo_full.py
    ... --focus             # ingest ONLY BPM + Mule (clean Mule-only story)
    ... --include-simulator # also ingest the synthetic simulator source
    ... --keep              # don't reset first (append to existing data)
    ... --keep-servicenow   # don't strip the ServiceNow sink (emits its noise)
    ... --tenant 1
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# serverops root = parent of this script's dir (scripts/)
SERVEROPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(SERVEROPS_ROOT, ".env"))   # ensure DATABASE_URL is set
sys.path.insert(0, SERVEROPS_ROOT)

# Windows consoles default to cp1252 and choke on Unicode; force UTF-8 output.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import func, select

from app.db.models import EventLog, Finding, RemediationAction, RootCauseReport
from app.db.session import SessionLocal
from app.dev_reset import reset
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.investigator_dispatcher import InvestigatorDispatcher
from app.orchestrator.registry import Registry
from app.paths import PROJECT_ROOT
from app.readers.persistence import persist_events
from app.observability import bootstrap as _obs_bootstrap

# POINT 21 (Task #21): wire the live stage emitter (+ optional OTel/Langfuse).
_obs_bootstrap()


CONFIG_PATH = PROJECT_ROOT / "config" / "modules.yaml"

# The crisp "Mule makes it definitive" story: just the BPM symptoms + Mule's
# deterministic localization, without infra sources introducing a competing
# "the webapp itself is exhausted" narrative.
FOCUS_SOURCES = {"appian_integration_trace", "appian_task_errors", "mule"}


def strip_servicenow(registry: Registry) -> None:
    """Drop ServiceNow sinks at runtime so the demo never calls ServiceNow.

    Avoids both the DNS error (real mode, unreachable instance) and the
    varchar-64 truncation in the mock back-link path — without touching the
    real .env or modules.yaml. The report/actions still persist normally.
    """
    for inv in registry.investigators():
        inv.sink_class_paths = [
            p for p in inv.sink_class_paths if "servicenow" not in p.lower()
        ]


def banner(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def _vals(jsonb_field) -> list:
    if isinstance(jsonb_field, dict):
        return jsonb_field.get("values") or []
    if isinstance(jsonb_field, list):
        return jsonb_field
    return []


def ingest(registry: Registry, tenant_id: int, skip: set[str]) -> None:
    banner("STAGE 1 — INGEST  (raw files → event_log)")
    for source_type in registry.source_types():
        if source_type in skip:
            print(f"  [skip] {source_type}")
            continue
        reader = registry.build_reader(source_type, tenant_id=tenant_id)
        payloads = list(reader.read())
        if not payloads:
            print(f"  [{source_type}] no new events")
            continue
        with SessionLocal() as s:
            n = persist_events(s, payloads)
        reader.commit()
        print(f"  [{source_type}] ingested {n} event(s)")

    with SessionLocal() as s:
        rows = s.execute(
            select(EventLog.source_type, func.count())
            .group_by(EventLog.source_type).order_by(EventLog.source_type)
        ).all()
    print("\n  event_log now holds:")
    for src, cnt in rows:
        print(f"     {src:<28} {cnt}")


def dispatch(registry: Registry, tenant_id: int) -> None:
    banner("STAGE 2 — DISPATCH  (event_log → finding)")
    with SessionLocal() as s:
        n = Dispatcher(registry, tenant_ids=[tenant_id]).run_once(s)
    print(f"  analyzed {n} event row(s) in one pass\n")

    with SessionLocal() as s:
        findings = list(s.scalars(
            select(Finding).order_by(Finding.source_type, Finding.severity.desc())
        ).all())
    print(f"  {len(findings)} finding(s) produced:")
    for f in findings:
        cids = _vals(f.case_ids)
        print(f"     [{f.severity:<8}] {f.source_type:<26} subject={f.subject_key}")
        print(f"                 case_ids={cids}")
        if f.observation:
            print(f"                 → {f.observation[:110]}")


def investigate(registry: Registry, tenant_id: int) -> None:
    banner("STAGE 3 + 4 — GROUP INTO INCIDENTS & EXPLAIN  (finding → root_cause_report)")
    with SessionLocal() as s:
        n = InvestigatorDispatcher(registry, tenant_ids=[tenant_id]).run_once(s)
    print(f"  processed {n} finding(s) into incident(s)\n")

    with SessionLocal() as s:
        reports = list(s.scalars(
            select(RootCauseReport).order_by(RootCauseReport.produced_at.desc())
        ).all())
    print(f"  {len(reports)} report(s):")
    for r in reports:
        payload = r.payload or {}
        srcs = payload.get("incident_sources") or []
        merged = ("mule" in srcs) and any("appian" in str(x) for x in srcs)
        flag = "   <== MULE + APPIAN MERGED" if merged else ""
        trigger = (payload.get("primary_trigger") or {}).get("source_type")
        print(f"\n  report_id={r.report_id}  severity={r.severity}  primary_trigger={trigger}")
        print(f"     incident_sources = {srcs}{flag}")
        print(f"     summary: {r.summary}")


def remediation_summary() -> None:
    banner("STAGE 5 — RECOMMENDED ACTIONS  (root_cause_report → remediation_action)")
    with SessionLocal() as s:
        actions = list(s.scalars(
            select(RemediationAction).order_by(RemediationAction.action_id.desc())
        ).all())
    print(f"  {len(actions)} action(s):")
    for a in actions:
        print(f"     [{a.action_type:<10} {a.state:<8}] {a.action_text[:90]}")
        if a.command:
            print(f"                 $ {a.command}")


def main() -> None:
    parser = argparse.ArgumentParser(description="One-shot full-pipeline demo (all sources)")
    parser.add_argument("--tenant", type=int, default=1)
    parser.add_argument("--keep", action="store_true", help="do NOT reset the DB first")
    parser.add_argument("--include-simulator", action="store_true",
                        help="also ingest the synthetic simulator source")
    parser.add_argument("--focus", action="store_true",
                        help="ingest only BPM + Mule sources for a clean downstream-localized incident")
    parser.add_argument("--keep-servicenow", action="store_true",
                        help="do NOT strip the ServiceNow sink (will emit DNS/truncation noise)")
    # See ALL component logs: --log-level INFO (or DEBUG); --sql also echoes SQL.
    parser.add_argument("--log-level", default="WARNING",
                        help="DEBUG | INFO | WARNING (default) — set INFO/DEBUG to see every component's logs")
    parser.add_argument("--sql", action="store_true", help="also echo every SQLAlchemy SQL statement")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.sql:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.INFO)

    registry = Registry.load(CONFIG_PATH)

    if args.focus:
        skip = {st for st in registry.source_types() if st not in FOCUS_SOURCES}
        print(f"  [focus mode] ingesting only: {sorted(FOCUS_SOURCES)}")
    else:
        skip = set() if args.include_simulator else {"simulator"}

    if not args.keep_servicenow:
        strip_servicenow(registry)

    if not args.keep:
        banner("STAGE 0 — RESET  (clean slate)")
        reset()
    ingest(registry, args.tenant, skip)
    dispatch(registry, args.tenant)
    investigate(registry, args.tenant)
    remediation_summary()

    banner("DONE")
    print("  Look for a report above whose incident_sources includes BOTH 'mule'")
    print("  and an 'appian_*' source — that is the cross-source merge, and its")
    print("  summary should localize the failure to the downstream system.\n")


if __name__ == "__main__":
    main()
