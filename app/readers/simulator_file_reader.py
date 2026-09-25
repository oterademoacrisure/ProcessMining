from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.paths import resolve_under_project
from app.readers.base import BaseReader, EventLogPayload
from app.readers.reader_state import load_state, save_state


class SimulatorFileReader(BaseReader):
    source_type = "simulator"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        input_dir = self.config.get("input_dir", "data/input/simulator")
        self.input_dir: Path = resolve_under_project(input_dir)
        self._pending_files: list[str] = []

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_files = []
        if not self.input_dir.exists():
            return

        state = load_state(self.source_type)
        processed: set[str] = set(state.get("processed_files", []))

        new_files = sorted(
            p for p in self.input_dir.glob("*.jsonl") if p.name not in processed
        )

        for path in new_files:
            with path.open("r", encoding="utf-8") as f:
                for line_num, raw in enumerate(f, start=1):
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        record = json.loads(raw)
                    except json.JSONDecodeError as e:
                        raise ValueError(
                            f"Malformed JSON at {path.name}:{line_num} — {e}"
                        ) from e
                    yield self._to_payload(record)
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

    def _to_payload(self, record: dict) -> EventLogPayload:
        ts = record.get("timestamp")
        if isinstance(ts, str):
            timestamp = datetime.fromisoformat(ts)
        elif isinstance(ts, datetime):
            timestamp = ts
        else:
            raise ValueError(f"Simulator record missing 'timestamp': {record!r}")

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=timestamp,
            activity_name="metric_sample",
            activity_type="automated",
            lifecycle_stage="complete",
            system_id=record.get("system_id"),
            server_id=record.get("hostname"),
            metadata_json=record,
        )
