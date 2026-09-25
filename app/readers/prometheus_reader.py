from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.paths import resolve_under_project
from app.readers.base import BaseReader, EventLogPayload
from app.readers.reader_state import load_state, save_state
from app.readers.timestamps import parse_iso_timestamp


class PrometheusReader(BaseReader):
    """Reads Prometheus node-metric snapshot JSON files from an input directory.

    Each input file holds either a single snapshot object or a list of them.
    The reader normalizes the nested Prometheus shape into a flat metrics dict
    placed in `metadata_json`, with the original record preserved under
    `metadata_json["raw"]` so analyzers can drill in if needed.
    """

    source_type = "prometheus"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        input_dir = self.config.get("input_dir", "data/input/prometheus")
        self.input_dir: Path = resolve_under_project(input_dir)
        self._pending_files: list[str] = []

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_files = []
        if not self.input_dir.exists():
            return

        state = load_state(self.source_type)
        processed: set[str] = set(state.get("processed_files", []))

        new_files = sorted(
            p for p in self.input_dir.glob("*.json") if p.name not in processed
        )

        for path in new_files:
            with path.open("r", encoding="utf-8") as f:
                try:
                    data = json.load(f)
                except json.JSONDecodeError as e:
                    raise ValueError(f"Malformed JSON in {path.name}: {e}") from e
            entries = data if isinstance(data, list) else [data]
            file_scrape_time = datetime.now(timezone.utc)
            for entry in entries:
                ts_raw = entry.get("scrape_time") or entry.get("timestamp")
                ts = parse_iso_timestamp(ts_raw) if ts_raw else file_scrape_time
                yield self._to_payload(entry, ts)
            self._pending_files.append(path.name)

    def commit(self) -> None:
        if not self._pending_files:
            return
        state = load_state(self.source_type)
        processed = set(state.get("processed_files", []))
        processed.update(self._pending_files)
        state["processed_files"] = sorted(processed)
        save_state(self.source_type, state)
        self._pending_files = []

    def _to_payload(self, record: dict, scrape_time: datetime) -> EventLogPayload:
        normalized = _normalize(record)

        hostname = None
        instance = record.get("instance")
        if isinstance(instance, str):
            hostname = instance.split(":")[0]

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=scrape_time,
            activity_name="metric_sample",
            activity_type="automated",
            lifecycle_stage="complete",
            system_id=record.get("system_id"),
            server_id=hostname,
            metadata_json=normalized,
        )


def _normalize(record: dict) -> dict:
    """Flatten the Prometheus shape into the same key set the simulator emits,
    so the prometheus_metrics_analyzer can read both uniformly."""
    cpu = record.get("cpu") or {}
    mem = record.get("memory") or {}
    fs = record.get("filesystem") or {}
    net = record.get("network") or {}

    mem_total = mem.get("total_bytes") or 0
    mem_avail = mem.get("available_bytes") or 0
    mem_pct = (
        round(((mem_total - mem_avail) / mem_total) * 100, 1) if mem_total else None
    )

    fs_size = fs.get("size_bytes") or 0
    fs_avail = fs.get("avail_bytes") or 0
    fs_pct = (
        round(((fs_size - fs_avail) / fs_size) * 100, 1) if fs_size else None
    )

    cpu_pct = cpu.get("usage_pct")
    iowait_pct = cpu.get("iowait_pct") or 0

    if iowait_pct > 20:
        wait_type = "IO"
    elif (cpu_pct or 0) > 50:
        wait_type = "CPU"
    else:
        wait_type = "None"

    return {
        "system_id":            record.get("system_id"),
        "application_name":     record.get("application_name"),
        "hostname":             (record.get("instance") or "").split(":")[0] or None,
        "tier":                 str(record.get("tier")) if record.get("tier") is not None else None,
        "cpu_allocated_vcpu":   cpu.get("count"),
        "cpu_usage_pct":        int(round(cpu_pct)) if cpu_pct is not None else None,
        "cpu_iowait_pct":       iowait_pct,
        "memory_allocated_gb":  round(mem_total / (1024 ** 3), 1) if mem_total else None,
        "memory_usage_pct":     int(round(mem_pct)) if mem_pct is not None else None,
        "storage_allocated_gb": round(fs_size / (1024 ** 3), 1) if fs_size else None,
        "storage_usage_pct":    int(round(fs_pct)) if fs_pct is not None else None,
        "active_connections":   net.get("tcp_active_connections"),
        "slow_query_count":     0,
        "wait_type":            wait_type,
        "raw":                  record,
    }
