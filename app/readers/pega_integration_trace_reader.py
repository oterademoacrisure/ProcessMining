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


class PegaIntegrationTraceReader(AppianCsvReader):
    """Reads Pega's integration trace log (pega_integration_trace.csv).

    Every row carries Case ID (the case key) and Operator ID (the actor key).
    `process_definition_name` comes from config (defaults to 'pega_default'),
    enabling lookup-or-create of ProcessDefinition + ProcessCase via the
    persistence layer.

    Columns: Timestamp, Interaction ID, Service Name, Operation, Operator ID,
    Case ID, Success, Timeout, HTTP Status, Total Time (ms), Remote System,
    Error Message.
    """

    source_type = "pega_integration_trace"
    log_filename_glob = "pega_integration_trace.csv"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        self.process_definition_name = self.config.get(
            "process_definition_name", "pega_default"
        )

    def to_payload(self, row: dict, timestamp: datetime) -> EventLogPayload:
        case_id_raw = row.get("Case ID")
        case_reference_id = (
            str(case_id_raw).strip()
            if case_id_raw not in (None, "", "null")
            else None
        )

        duration_ms = _maybe_float(row.get("Total Time (ms)"))
        duration_sec = duration_ms / 1000.0 if duration_ms is not None else None

        http_status = _maybe_int(row.get("HTTP Status"))
        success = _truthy(row.get("Success"))
        timeout = _truthy(row.get("Timeout"))

        service_name = row.get("Service Name")
        operation = row.get("Operation")

        metadata = {
            "interaction_id": row.get("Interaction ID"),
            "service_name":   service_name,
            "operation":      operation,
            "success":        success,
            "timeout":        timeout,
            "http_status":    http_status,
            "total_time_ms":  duration_ms,
            "remote_system":  row.get("Remote System"),
            "error_message":  row.get("Error Message") or None,
            "operator_id":    row.get("Operator ID"),
        }

        # Activity-name = the Pega service being invoked.
        # 07 July Ajinkya added a comment
        activity = service_name or row.get("Interaction ID") or "integration_call"

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=timestamp,
            activity_name=activity,
            activity_type="automated",
            lifecycle_stage="complete",
            actor_id=row.get("Operator ID") or None,
            system_id="pega",
            server_id=None,
            duration=duration_sec,
            metadata_json=metadata,
            case_reference_id=case_reference_id,
            process_definition_name=self.process_definition_name if case_reference_id else None,
        )
