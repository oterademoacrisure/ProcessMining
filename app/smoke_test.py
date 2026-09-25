"""End-to-end smoke test for the new pluggable pipeline.

Runs in a single process — no terminals, no sleeping, no infinite loops:

  1. Drops a hand-crafted simulator JSONL file into data/input/simulator/.
  2. Runs one ingest cycle (reader → persist_events → reader.commit).
  3. Runs one dispatch cycle (poll → analyzer → ConsoleSink → mark_processed).
  4. Queries EVENT_LOG to confirm rows landed with correct fields and got
     marked processed.

Pre-reqs (you must do these once before running):

    pip install -r requirements.txt
    alembic upgrade head        # creates schema + seeds tenant_id=1

Run:

    python app/smoke_test.py
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import func, select

from app.db.models import EventLog, Tenant
from app.db.session import SessionLocal
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.registry import Registry
from app.paths import INPUT_DIR, PROJECT_ROOT, STATE_DIR
from app.readers.persistence import persist_events


CONFIG_PATH = PROJECT_ROOT / "config" / "modules.yaml"
SIM_DIR = INPUT_DIR / "simulator"
SIM_STATE = STATE_DIR / "simulator.json"


def _check(condition: bool, label: str) -> None:
    mark = "OK " if condition else "FAIL"
    print(f"  [{mark}] {label}")
    if not condition:
        raise SystemExit(1)


def _seed_input_file() -> Path:
    SIM_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    path = SIM_DIR / f"tick-{stamp}-smoketest.jsonl"
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        {
            "timestamp": now_iso,
            "system_id": "smoketest-host-01",
            "application_name": "SmokeTest_App",
            "hostname": "smoketest-host-01",
            "tier": "1",
            "cpu_allocated_vcpu": 4,
            "cpu_usage_pct": 92,
            "memory_allocated_gb": 16.0,
            "memory_usage_pct": 88,
            "storage_allocated_gb": 256.0,
            "storage_usage_pct": 45,
            "active_connections": 120,
            "slow_query_count": 3,
            "wait_type": "CPU",
        },
        {
            "timestamp": now_iso,
            "system_id": "smoketest-host-02",
            "application_name": "SmokeTest_App2",
            "hostname": "smoketest-host-02",
            "tier": "2",
            "cpu_allocated_vcpu": 8,
            "cpu_usage_pct": 20,
            "memory_allocated_gb": 32.0,
            "memory_usage_pct": 35,
            "storage_allocated_gb": 512.0,
            "storage_usage_pct": 28,
            "active_connections": 15,
            "slow_query_count": 0,
            "wait_type": "None",
        },
    ]
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    print(f"  seeded {path.name} ({len(rows)} rows)")
    return path


def main() -> None:
    print("== Smoke test ==")

    # Pre-flight: tenant_id=1 must exist.
    print("\n[1/5] Verify tenant seed")
    with SessionLocal() as session:
        t = session.scalar(select(Tenant).where(Tenant.tenant_id == 1))
        _check(t is not None, "Tenant(tenant_id=1) exists (did you run `alembic upgrade head`?)")

    # Clean up any leftover state from prior runs of this script.
    print("\n[2/5] Reset simulator state file")
    if SIM_STATE.exists():
        SIM_STATE.unlink()
        print(f"  removed {SIM_STATE}")
    else:
        print(f"  no prior state at {SIM_STATE}")

    # Seed one input file.
    print("\n[3/5] Seed simulator input file")
    seeded = _seed_input_file()

    # Capture row count before to scope our assertions.
    with SessionLocal() as session:
        before = session.scalar(select(func.count()).select_from(EventLog)) or 0

    # Run one ingest cycle for the simulator source only.
    print("\n[4/5] Ingest + dispatch")
    registry = Registry.load(CONFIG_PATH)
    reader = registry.build_reader("simulator", tenant_id=1)
    payloads = list(reader.read())
    _check(len(payloads) == 2, f"reader yielded 2 payloads (got {len(payloads)})")

    with SessionLocal() as session:
        persisted = persist_events(session, payloads)
    reader.commit()
    _check(persisted == 2, f"persist_events wrote 2 rows (got {persisted})")
    _check(SIM_STATE.exists(), "state file written by reader.commit()")

    state = json.loads(SIM_STATE.read_text(encoding="utf-8"))
    _check(seeded.name in state.get("processed_files", []),
           f"state file lists {seeded.name} as processed")

    dispatcher = Dispatcher(registry, tenant_ids=[1])
    with SessionLocal() as session:
        dispatched = dispatcher.run_once(session)
    _check(dispatched == 2, f"dispatcher processed 2 rows (got {dispatched})")

    # Verify DB state.
    print("\n[5/5] Verify EVENT_LOG state")
    with SessionLocal() as session:
        after = session.scalar(select(func.count()).select_from(EventLog)) or 0
        _check(after - before == 2, f"EVENT_LOG grew by 2 ({before} -> {after})")

        critical_row = session.scalar(
            select(EventLog)
            .where(EventLog.system_id == "smoketest-host-01")
            .order_by(EventLog.event_id.desc())
            .limit(1)
        )
        _check(critical_row is not None, "smoketest-host-01 row found")
        _check(critical_row.source_type == "simulator", "source_type=simulator")
        _check(critical_row.tenant_id == 1, "tenant_id=1")
        _check(critical_row.processed_at is not None, "processed_at stamped by dispatcher")
        _check(isinstance(critical_row.metadata_json, dict)
               and critical_row.metadata_json.get("cpu_usage_pct") == 92,
               "metadata_json round-tripped cpu_usage_pct=92")

    print("\n== Smoke test PASSED ==")


if __name__ == "__main__":
    main()
