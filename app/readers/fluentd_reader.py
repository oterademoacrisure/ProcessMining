from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from app.paths import resolve_under_project
from app.readers.base import BaseReader, EventLogPayload
from app.readers.reader_state import load_state, save_state
from app.readers.timestamps import parse_iso_timestamp


class FluentdReader(BaseReader):
    """Reads Fluentd-style JSONL log files from a directory.

    One JSON object per line. Tracks byte offset per file so appended events
    are picked up on the next poll without re-emission. State shape:
        { "offsets": { "<filename>": <byte_offset>, ... } }
    """

    source_type = "fluentd"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        input_dir = self.config.get("input_dir", "data/input/fluentd")
        self.input_dir: Path = resolve_under_project(input_dir)
        self.file_glob = self.config.get("file_glob", "*.jsonl")
        self._pending_offsets: dict[str, int] = {}

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_offsets = {}
        if not self.input_dir.exists():
            return

        state = load_state(self.source_type)
        offsets: dict[str, int] = dict(state.get("offsets") or {})

        for path in sorted(self.input_dir.glob(self.file_glob)):
            yield from self._read_file(path, offsets)

    def _read_file(self, path: Path, offsets: dict[str, int]) -> Iterable[EventLogPayload]:
        size = path.stat().st_size
        offset = offsets.get(path.name, 0)
        if offset > size:
            offset = 0
        if offset == size:
            self._pending_offsets[path.name] = offset
            return

        with path.open("r", encoding="utf-8") as f:
            f.seek(offset)
            new_data = f.read()
            new_offset = f.tell()

        for line in new_data.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = self._to_payload(record)
            if payload is not None:
                yield payload

        self._pending_offsets[path.name] = new_offset

    def _to_payload(self, record: dict) -> EventLogPayload | None:
        ts_raw = record.get("@timestamp") or record.get("timestamp")
        if not ts_raw:
            return None
        try:
            ts = parse_iso_timestamp(ts_raw)
        except ValueError:
            return None

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=ts,
            activity_name=record.get("logger") or "log_event",
            activity_type="automated",
            lifecycle_stage="complete",
            system_id=record.get("service"),
            server_id=record.get("host"),
            metadata_json=record,
        )

    def commit(self) -> None:
        if not self._pending_offsets:
            return
        state = load_state(self.source_type)
        offsets: dict[str, int] = dict(state.get("offsets") or {})
        offsets.update(self._pending_offsets)
        state["offsets"] = offsets
        save_state(self.source_type, state)
        self._pending_offsets = {}
