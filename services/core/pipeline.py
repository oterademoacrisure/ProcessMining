"""APIFICATION: background pipeline loop for core-service.

Mirrors app/runner.py :: run(), but the ingest step now polls reader-service
over HTTP instead of building readers in-process. The dispatch / investigate /
remediate cycles are unchanged (same registry, same dispatchers, same DB).

Runs on a daemon thread started at FastAPI startup.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

from app.db.session import SessionLocal
from app.observability import progress
from app.observability.telemetry import init_telemetry
from app.orchestrator.dispatcher import Dispatcher
from app.orchestrator.investigator_dispatcher import InvestigatorDispatcher
from app.orchestrator.registry import Registry
from app.orchestrator.remediation_dispatcher import RemediationDispatcher
from app.paths import PROJECT_ROOT
from app.readers.persistence import persist_events

from services.core.reader_client import ReaderClient


log = logging.getLogger("core.pipeline")


class Pipeline:
    def __init__(
        self,
        *,
        config_path: Path,
        tenant_id: int,
        interval_sec: int,
        reader_client: ReaderClient,
    ) -> None:
        self.config_path = config_path
        self.tenant_id = tenant_id
        self.interval_sec = interval_sec
        self.reader_client = reader_client

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        registry = Registry.load(config_path)
        self.registry = registry
        self.dispatcher = Dispatcher(registry, tenant_ids=[tenant_id])
        self.inv_dispatcher = InvestigatorDispatcher(registry, tenant_ids=[tenant_id])
        self.rem_dispatcher = RemediationDispatcher(
            config=registry.remediation_config(), tenant_ids=[tenant_id]
        )

        obs_cfg = {"observability": registry.observability_config()}
        progress.configure(obs_cfg)
        init_telemetry(obs_cfg)

    # ── lifecycle ────────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="core-pipeline", daemon=True)
        self._thread.start()
        log.info("pipeline started — tenant=%s interval=%ss reader=%s",
                 self.tenant_id, self.interval_sec, self.reader_client.base_url)

    def stop(self, join_timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=join_timeout)

    # ── one cycle ────────────────────────────────────────────────────────────
    def _ingest(self) -> int:
        total = 0
        try:
            source_types = self.reader_client.list_sources()
        except Exception as e:
            log.warning("reader list_sources failed: %s", e)
            return 0

        for source_type in source_types:
            try:
                batch_id, payloads = self.reader_client.poll(source_type, self.tenant_id)
            except Exception as e:
                log.warning("reader poll failed for %s: %s", source_type, e)
                continue
            if not payloads:
                continue
            try:
                with SessionLocal() as s:
                    n = persist_events(s, payloads)
            except Exception:
                log.exception("persist_events failed for %s — not acking (will retry)", source_type)
                continue
            try:
                if batch_id:
                    self.reader_client.ack(source_type, batch_id)
            except Exception as e:
                # Persisted rows are de-duped by natural key on next round; the
                # missed ack means we'll re-persist and re-de-dup. Non-fatal.
                log.warning("reader ack failed for %s (batch=%s): %s", source_type, batch_id, e)
            if n:
                log.info("[ingest:%s] persisted %d event(s)", source_type, n)
                total += n
        return total

    def _dispatch(self) -> int:
        with SessionLocal() as s:
            return self.dispatcher.run_once(s)

    def _investigate(self) -> int:
        with SessionLocal() as s:
            return self.inv_dispatcher.run_once(s)

    def _remediate(self) -> int:
        with SessionLocal() as s:
            return self.rem_dispatcher.run_once(s)

    # ── main loop ────────────────────────────────────────────────────────────
    def _loop(self) -> None:
        cycle = 0
        while not self._stop.is_set():
            cycle += 1
            try:
                ingested = self._ingest()
                dispatched = self._dispatch()
                investigated = self._investigate()
                remediated = self._remediate()
                if not (ingested or dispatched or investigated or remediated):
                    log.debug("cycle %d: nothing to do", cycle)
            except Exception:
                # Never let the loop die from a single bad cycle.
                log.exception("pipeline cycle %d crashed — continuing", cycle)

            # Sleep in short slices so stop() is responsive.
            for _ in range(self.interval_sec * 10):
                if self._stop.is_set():
                    break
                time.sleep(0.1)
        log.info("pipeline stopped")


# ── factory / config ─────────────────────────────────────────────────────────
def build_pipeline() -> Pipeline:
    config_path = Path(os.getenv("MODULES_YAML", PROJECT_ROOT / "config" / "modules.yaml"))
    tenant_id = int(os.getenv("TENANT_ID", "1"))
    interval_sec = int(os.getenv("INTERVAL_SEC", "10"))
    reader_url = os.getenv("READER_SERVICE_URL", "http://reader-service:8100")
    return Pipeline(
        config_path=config_path,
        tenant_id=tenant_id,
        interval_sec=interval_sec,
        reader_client=ReaderClient(base_url=reader_url),
    )
