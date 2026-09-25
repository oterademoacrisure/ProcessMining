"""Single-process runner: ingests from every registered reader, then dispatches
unprocessed EVENT_LOG rows to their analyzers and sinks. Sleeps and repeats.

Usage:
    python app/runner.py
    python app/runner.py --interval 5
    python app/runner.py --interval 5 --duration 60
    python app/runner.py --config config/modules.yaml --tenant 1

This is the only entry point you need to run in Phase 1. Start the simulator
separately (it writes JSONL into data/input/simulator/), then start this runner.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.db.session import SessionLocal
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.investigator_dispatcher import InvestigatorDispatcher
from app.orchestrator.registry import Registry
from app.orchestrator.remediation_dispatcher import RemediationDispatcher
from app.paths import PROJECT_ROOT
from app.readers.persistence import persist_events


DEFAULT_CONFIG = PROJECT_ROOT / "config" / "modules.yaml"

_stop = False


def _install_signal_handlers() -> None:
    def handler(signum, frame):
        global _stop
        print(f"\nReceived signal {signum} — stopping after current cycle...")
        _stop = True

    signal.signal(signal.SIGINT, handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, handler)


def _ingest_cycle(registry: Registry, tenant_id: int) -> int:
    total = 0
    for source_type in registry.source_types():
        reader = registry.build_reader(source_type, tenant_id=tenant_id)
        payloads = list(reader.read())
        if not payloads:
            continue
        with SessionLocal() as session:
            n = persist_events(session, payloads)
        reader.commit()
        if n:
            print(f"  [ingest:{source_type}] persisted {n} event(s)")
            total += n
    return total


def _dispatch_cycle(dispatcher: Dispatcher) -> int:
    with SessionLocal() as session:
        return dispatcher.run_once(session)


def _investigate_cycle(inv_dispatcher: InvestigatorDispatcher) -> int:
    with SessionLocal() as session:
        return inv_dispatcher.run_once(session)


def _remediation_cycle(rem_dispatcher: RemediationDispatcher) -> int:
    with SessionLocal() as session:
        return rem_dispatcher.run_once(session)


def run(
    config_path: Path,
    tenant_id: int,
    interval_sec: int,
    duration_sec: int | None,
) -> None:
    registry = Registry.load(config_path)
    dispatcher = Dispatcher(registry, tenant_ids=[tenant_id])
    inv_dispatcher = InvestigatorDispatcher(registry, tenant_ids=[tenant_id])

    # Pull remediation config from the YAML (under top-level `remediation:`
    # block). Falls back to defaults (mock executor, restart-only allow-list)
    # if not configured.
    remediation_cfg = registry.remediation_config()
    rem_dispatcher = RemediationDispatcher(
        config=remediation_cfg, tenant_ids=[tenant_id]
    )

    # POINT 21 (Task #21): wire the live stage-event emitter (+ optional
    # OpenTelemetry/Langfuse) from the `observability:` config block.
    obs_cfg = {"observability": registry.observability_config()}
    from app.observability import progress
    from app.observability.telemetry import init_telemetry
    progress.configure(obs_cfg)
    init_telemetry(obs_cfg)

    print(f"Runner started — config={config_path}, tenant_id={tenant_id}, "
          f"interval={interval_sec}s"
          + (f", duration={duration_sec}s" if duration_sec else ", duration=infinite"))
    print(f"Sources: {registry.source_types()}")
    print(f"Investigators: {[i.name for i in registry.investigators()]}")
    print(f"Remediation executor: {remediation_cfg.get('executor_mode', 'mock')}")
    print("-" * 60)

    _install_signal_handlers()
    start = time.time()
    cycle = 0

    while not _stop:
        cycle += 1
        run_id = f"t{tenant_id}-c{cycle}"
        print(f"\n[Cycle {cycle}] {time.strftime('%H:%M:%S')}")

        ingested = _ingest_cycle(registry, tenant_id)
        dispatched = _dispatch_cycle(dispatcher)
        investigated = _investigate_cycle(inv_dispatcher)
        remediated = _remediation_cycle(rem_dispatcher)
        # POINT 21 (Task #21): INGEST/DETECT/CORRELATE events are emitted inside the
        # stage code (persist_events / Dispatcher / InvestigatorDispatcher), so they
        # fire for EVERY entry point (runner and demo_full) — not just here.

        if not ingested and not dispatched and not investigated and not remediated:
            print("  (nothing to do)")

        if duration_sec and (time.time() - start) >= duration_sec:
            print("\nDuration reached — runner stopped.")
            break

        for _ in range(interval_sec * 10):
            if _stop:
                break
            time.sleep(0.1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Multi-tenant process mining runner")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--tenant", type=int, default=1)
    parser.add_argument("--interval", type=int, default=10)
    parser.add_argument("--duration", type=int, default=None)
    args = parser.parse_args()

    run(
        config_path=args.config,
        tenant_id=args.tenant,
        interval_sec=args.interval,
        duration_sec=args.duration,
    )
