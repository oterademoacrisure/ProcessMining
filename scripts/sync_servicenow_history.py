"""POINT 19 (Task #19): sync resolved ServiceNow incidents into historical_incident.

Ties together the client (fetch) and loader (store). Run it once to backfill,
then on a schedule (cron / Task Scheduler / cloud job) to keep history current —
it's idempotent and incremental, so re-runs are cheap and safe.

Incremental watermark is DERIVED FROM THE DATA (no separate state file):
    since = MAX(sys_updated_on) for this tenant
  - empty table  -> since is NULL -> full backfill (automatic)
  - otherwise    -> pull only incidents updated at/after the newest we have
Boundary rows may be re-pulled; the loader's idempotent upsert makes that harmless.

Usage:
    python scripts/sync_servicenow_history.py                 # incremental (full on first run)
    python scripts/sync_servicenow_history.py --full          # force a full re-pull
    python scripts/sync_servicenow_history.py --tenant 2      # a different customer's instance
    python scripts/sync_servicenow_history.py --max 50        # cap rows (testing)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

# POINT 19: resolve serverops paths so the script runs from anywhere.
SERVEROPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(SERVEROPS_ROOT, ".env"))
sys.path.insert(0, SERVEROPS_ROOT)

from sqlalchemy import func, select

from app.db.models import HistoricalIncident
from app.db.session import SessionLocal
from app.servicenow_history.client import ServiceNowHistoryClient
from app.servicenow_history.loader import load_incidents


log = logging.getLogger("sync_servicenow_history")


def _current_watermark(tenant_id: int):
    """POINT 19: the incremental boundary = newest source-update time we've stored."""
    with SessionLocal() as s:
        return s.scalar(
            select(func.max(HistoricalIncident.sys_updated_on))
            .where(HistoricalIncident.tenant_id == tenant_id)
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Sync ServiceNow resolved incidents (Task #19)")
    parser.add_argument("--tenant", type=int, default=1, help="which customer this ServiceNow instance maps to")
    parser.add_argument("--full", action="store_true", help="force a full backfill (ignore the watermark)")
    parser.add_argument("--max", type=int, default=None, help="cap total rows (testing)")
    args = parser.parse_args()

    # POINT 19: decide backfill vs incremental from the DB-derived watermark.
    since = None if args.full else _current_watermark(args.tenant)
    mode = "FULL backfill" if since is None else f"INCREMENTAL since {since}"
    print(f"ServiceNow history sync — tenant={args.tenant} — {mode}")

    client = ServiceNowHistoryClient()
    if not client.is_configured():
        print("ERROR: SERVICENOW_* env vars not set in .env — cannot sync.")
        raise SystemExit(1)

    records = client.fetch_resolved_incidents(since=since, max_records=args.max)
    print(f"fetched {len(records)} resolved incident(s) from ServiceNow")

    with SessionLocal() as s:
        result = load_incidents(s, args.tenant, records)
        s.commit()   # POINT 19: whole batch commits together (watermark advances only on success)

    new_wm = _current_watermark(args.tenant)
    print(
        f"done — inserted={result['inserted']} updated={result['updated']} "
        f"skipped={result['skipped']} | new watermark={new_wm}"
    )


if __name__ == "__main__":
    main()
