"""Pydantic v2 schema for a single Mulesoft runtime event.

Ported verbatim from mule-rca/src/schemas.py. One inbound request to Mule
typically produces ~4 events sharing one `correlation_id`:

    flow.start -> http.request -> http.response | http.error -> flow.end | flow.error

The schema uses `extra="ignore"` so future Mule versions can add fields
without breaking the reader (integration point #1 — flip to "forbid" if
strict drift-detection is wanted).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


EventType = Literal[
    "flow.start",
    "flow.end",
    "flow.error",
    "processor.start",
    "processor.end",
    "http.request",
    "http.response",
    "http.error",
    "retry",
    "log",
]

LogLevel = Literal["DEBUG", "INFO", "WARN", "ERROR"]

ExecutionStatus = Literal["SUCCESS", "FAILED", "TIMEOUT", "RETRY"]


class MuleIntegrationEvent(BaseModel):
    """One event from Mulesoft Anypoint Monitoring."""

    model_config = ConfigDict(extra="ignore")

    # --- required identity / context ---------------------------------------
    event_id: UUID
    tenant_id: str = "default"
    timestamp: datetime
    correlation_id: str
    flow_name: str
    application: str
    event_type: EventType
    log_level: LogLevel

    # --- runtime / processor context ---------------------------------------
    processor_name: str | None = None
    duration_ms: int | None = None
    status: ExecutionStatus | None = None

    # --- http context (populated when event_type starts with "http.") ------
    http_status: int | None = None
    http_method: str | None = None
    endpoint: str | None = None
    payload_size_bytes: int | None = None
    response_size_bytes: int | None = None

    # --- error context (populated when the event represents a failure) -----
    error_type: str | None = None
    error_message: str | None = None

    # --- cross-source linkage — the join keys into Appian's data -----------
    downstream_system: str | None = None
    appian_process_id: str | None = None
    user_uuid: str | None = None

    # --- runtime metadata --------------------------------------------------
    thread: str | None = None

    # --- constants ---------------------------------------------------------
    layer: Literal["integration_mule"] = "integration_mule"
    source_system: Literal["mulesoft_runtime"] = "mulesoft_runtime"
