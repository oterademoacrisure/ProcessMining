"""MuleReader: ingest Mulesoft runtime JSONL into the shared event_log table.

A tier-1 reader (like the Appian / Fluentd readers): it validates each JSONL
line against `MuleIntegrationEvent`, quarantines bad rows (never silently
drops them — §12 guardrail), and yields normalized `EventLogPayload`s. The
runner persists them via `persist_events`.

THE CRITICAL MAPPING — cross-source correlation:
    appian_process_id  ->  case_reference_id   (+ process_definition_name)

Because the Appian integration_trace reader maps its `Process ID` to the same
`case_reference_id` under `process_definition_name="appian_default"`,
`persist_events` resolves both to the SAME numeric `case_id`. That shared
case_id is what lets the incident grouper merge Mule findings with Appian
findings into one incident — turning a probable cause into a definitive one.
Keep `process_definition_name` here in lockstep with the appian_integration_trace
module config.

Integration-point resolutions (vs mule-rca/docs/integration.md):
  #1 schema strictness  -> MuleIntegrationEvent keeps extra="ignore"; bad rows quarantined.
  #2 registry attrs     -> `source_type` class attr (no source_name/target_table).
  #3 DDL auto-create    -> dropped; serverops owns the schema (Alembic + event_log).
  #4 tenant_id          -> STAMP the reader's int tenant_id.
  #5 single-row INSERT  -> dropped; runner batches via persist_events.
  #6 register_with()    -> dropped; wired via config/modules.yaml instead.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

from pydantic import ValidationError

from app.mule.schemas import MuleIntegrationEvent
from app.paths import resolve_under_project
from app.readers.base import BaseReader, EventLogPayload
from app.readers.reader_state import load_state, save_state


log = logging.getLogger(__name__)


class MuleReader(BaseReader):
    """Reads Mulesoft Anypoint-style JSONL log files from a directory.

    One JSON object per line. Tracks byte offset per file so appended events
    are picked up on the next poll without re-emission. State shape:
        { "offsets": { "<filename>": <byte_offset>, ... } }

    Config keys (all optional):
      - input_dir:                base dir; defaults to "data/input/mule".
      - file_glob:                defaults to "*.jsonl".
      - process_definition_name:  natural-key namespace for case resolution;
                                  MUST match the appian_integration_trace module
                                  (defaults to "appian_default").
      - server_id:               default server_id stamped on every payload.
                                  Defaults to None — Mule correlation rides on
                                  case_id, not host, so leave unset unless you
                                  deliberately want host-level linkage.
    """

    source_type = "mule"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        input_dir = self.config.get("input_dir", "data/input/mule")
        self.input_dir: Path = resolve_under_project(input_dir)
        self.file_glob = self.config.get("file_glob", "*.jsonl")
        self.process_definition_name = self.config.get(
            "process_definition_name", "appian_default"
        )
        self.default_server_id: str | None = self.config.get("server_id")
        self._pending_offsets: dict[str, int] = {}
        # Bad rows surface here for inspection/tests; also logged, never dropped.
        self.quarantined: list[tuple[str, str]] = []

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_offsets = {}
        self.quarantined = []
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
            payload = self._to_payload(line)
            if payload is not None:
                yield payload

        self._pending_offsets[path.name] = new_offset

    def _to_payload(self, raw: str) -> EventLogPayload | None:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as e:
            self._quarantine(raw, f"JSONDecodeError: {e}")
            return None
        try:
            event = MuleIntegrationEvent.model_validate(record)
        except ValidationError as e:
            self._quarantine(raw, f"ValidationError: {_summarize_validation_error(e)}")
            return None

        case_reference_id = (
            str(event.appian_process_id).strip()
            if event.appian_process_id not in (None, "")
            else None
        )

        duration_sec = event.duration_ms / 1000.0 if event.duration_ms is not None else None

        return EventLogPayload(
            tenant_id=self.tenant_id,          # #4 stamp the reader's int tenant
            source_type=self.source_type,
            timestamp=event.timestamp,
            activity_name=event.flow_name,
            activity_type="automated",
            lifecycle_stage="complete",
            actor_id=event.user_uuid,
            system_id="mulesoft",
            server_id=self.default_server_id,
            duration=duration_sec,
            # Full event (JSON-safe) so the analyzer can rehydrate it verbatim.
            metadata_json=event.model_dump(mode="json"),
            case_reference_id=case_reference_id,
            process_definition_name=(
                self.process_definition_name if case_reference_id else None
            ),
        )

    def _quarantine(self, raw_line: str, reason: str) -> None:
        """Record a bad row — never silently dropped (§12 guardrail)."""
        self.quarantined.append((raw_line, reason))
        log.warning("mule_reader: quarantined a row (%s)", reason)

    def commit(self) -> None:
        if not self._pending_offsets:
            return
        state = load_state(self.source_type)
        offsets: dict[str, int] = dict(state.get("offsets") or {})
        offsets.update(self._pending_offsets)
        state["offsets"] = offsets
        save_state(self.source_type, state)
        self._pending_offsets = {}


def _summarize_validation_error(e: ValidationError) -> str:
    """Compress a pydantic ValidationError into one line for the quarantine reason."""
    errs = e.errors()
    if not errs:
        return str(e)
    first = errs[0]
    loc = ".".join(str(x) for x in first.get("loc", ()))
    return f"{loc}: {first.get('msg', '')}" if loc else first.get("msg", str(e))
