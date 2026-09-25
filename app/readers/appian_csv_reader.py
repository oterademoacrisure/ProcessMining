from __future__ import annotations

import csv
from abc import abstractmethod
from io import StringIO
from pathlib import Path
from typing import Iterable

from app.paths import resolve_under_project
from app.readers.base import BaseReader, EventLogPayload
from app.readers.reader_state import load_state, save_state
from app.readers.timestamps import parse_iso_timestamp


class AppianCsvReader(BaseReader):
    """Shared base for Appian CSV log readers.

    Appian logs are appended to over time (not rotated per tick like the
    simulator). This base tracks a byte offset per file in
    `data/state/<source_type>.json` so each poll yields only rows added since
    the last successful commit.

    Subclasses provide:
      - `source_type` (class attribute)
      - `log_filename_glob` (e.g., "process.csv", "integration_trace.csv")
      - `to_payload(row, timestamp)` mapping a CSV row dict to an EventLogPayload

    Config keys (all optional):
      - input_dir:     base dir; defaults to "data/input/appian"
      - server_id:     hostname/pod the log came from. If set, becomes the
                       default `server_id` on every payload (subclass can override).
      - timestamp_column: which CSV column carries the per-row timestamp.
                         Defaults to "Timestamp".
    """

    log_filename_glob: str = "*.csv"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        input_dir = self.config.get("input_dir", "data/input/appian")
        self.input_dir: Path = resolve_under_project(input_dir)
        self.default_server_id: str | None = self.config.get("server_id")
        self.timestamp_column: str = self.config.get("timestamp_column", "Timestamp")
        self._pending_offsets: dict[str, int] = {}

    @abstractmethod
    def to_payload(self, row: dict, timestamp) -> EventLogPayload:
        """Map one CSV row (header-keyed dict) to an EventLogPayload."""
        ...

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_offsets = {}
        if not self.input_dir.exists():
            return

        state = load_state(self.source_type)
        offsets: dict[str, int] = dict(state.get("offsets") or {})

        matching = sorted(self.input_dir.glob(self.log_filename_glob))
        for path in matching:
            yield from self._read_file(path, offsets)

    def _read_file(self, path: Path, offsets: dict[str, int]) -> Iterable[EventLogPayload]:
        size = path.stat().st_size
        offset = offsets.get(path.name, 0)

        if offset > size:
            offset = 0
        if offset == size:
            self._pending_offsets[path.name] = offset
            return

        header = self._read_header(path)
        if header is None:
            return

        with path.open("r", encoding="utf-8", newline="") as f:
            if offset == 0:
                f.readline()  # skip header (readline does not disable tell() like next() does)
                offset = f.tell()
            else:
                f.seek(offset)

            remainder = f.read()
            new_offset = f.tell()

        if not remainder.strip():
            self._pending_offsets[path.name] = new_offset
            return

        reader = csv.DictReader(
            StringIO(remainder),
            fieldnames=header,
        )
        for row in reader:
            if row is None:
                continue
            ts_raw = row.get(self.timestamp_column)
            if not ts_raw:
                continue
            try:
                timestamp = parse_iso_timestamp(ts_raw.replace(" ", "T"))
            except ValueError:
                continue
            payload = self.to_payload(row, timestamp)
            if self.default_server_id and payload.server_id is None:
                payload.server_id = self.default_server_id
            yield payload

        self._pending_offsets[path.name] = new_offset

    def _read_header(self, path: Path) -> list[str] | None:
        with path.open("r", encoding="utf-8", newline="") as f:
            first_line = f.readline()
        if not first_line:
            return None
        return next(csv.reader([first_line]))

    def commit(self) -> None:
        if not self._pending_offsets:
            return
        state = load_state(self.source_type)
        offsets: dict[str, int] = dict(state.get("offsets") or {})
        offsets.update(self._pending_offsets)
        state["offsets"] = offsets
        save_state(self.source_type, state)
        self._pending_offsets = {}
