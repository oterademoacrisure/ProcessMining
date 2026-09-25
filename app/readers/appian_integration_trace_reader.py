from __future__ import annotations

from datetime import datetime

from app.readers.appian_csv_reader import AppianCsvReader
from app.readers.base import EventLogPayload


def _maybe_int(value):
    if value in (None, "", "null"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _maybe_float(value):
    if value in (None, "", "null"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _truthy(value) -> bool | None:
    if value is None or value == "":
        return None
    s = str(value).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return None


class AppianIntegrationTraceReader(AppianCsvReader):
    """Reads Appian's integration trace log (integration_trace.csv).

    Every row carries Process ID (the case key) and Username (the actor key).
    `process_definition_name` comes from config (defaults to 'appian_default'),
    enabling lookup-or-create of ProcessDefinition + ProcessCase via the
    persistence layer.
    """

    source_type = "appian_integration_trace"
    log_filename_glob = "integration_trace.csv"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        self.process_definition_name = self.config.get(
            "process_definition_name", "appian_default"
        )

    def to_payload(self, row: dict, timestamp: datetime) -> EventLogPayload:
        process_id_raw = row.get("Process ID")
        case_reference_id = (
            str(process_id_raw).strip()
            if process_id_raw not in (None, "", "null")
            else None
        )

        duration_ms = _maybe_float(row.get("Total Time (ms)"))
        duration_sec = duration_ms / 1000.0 if duration_ms is not None else None

        status_code = _maybe_int(row.get("Status Code"))
        success = _truthy(row.get("Success"))
        timeout = _truthy(row.get("Timeout"))

        metadata = {
            "integration_uuid":      row.get("Integration UUID"),
            "operation":             row.get("Operation/Method"),
            "success":               success,
            "timeout":               timeout,
            "status_code":           status_code,
            "total_time_ms":         duration_ms,
            "prepare_time_ms":       _maybe_float(row.get("Prepare Time (ms)")),
            "execute_time_ms":       _maybe_float(row.get("Execute time (ms)")),
            "transform_time_ms":     _maybe_float(row.get("Transform Time (ms)")),
            "connected_system_uuid": row.get("Connected System UUID"),
            "username":              row.get("Username"),
        }

        # Activity-name = integration logical name (operation when present).
        activity = row.get("Operation/Method") or row.get("Integration UUID") or "integration_call"

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=timestamp,
            activity_name=activity,
            activity_type="automated",
            lifecycle_stage="complete",
            actor_id=row.get("Username") or None,
            system_id="appian",
            server_id=None,
            duration=duration_sec,
            metadata_json=metadata,
            case_reference_id=case_reference_id,
            process_definition_name=self.process_definition_name if case_reference_id else None,
        )
