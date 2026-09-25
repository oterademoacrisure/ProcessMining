from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.db.models import EventLog


@dataclass
class Finding:
    """One per-subject result from a per-source analyzer.

    The analyzer fills `severity`, `subject_key`, `observation`, `payload`,
    and the rows it actually used to reach this conclusion. Everything else
    (tenant_id, source_type, analyzer_class, correlation_hash, FK ids…)
    is enriched downstream by the emitter, which has access to dispatcher
    context.
    """

    severity: str  # "normal" | "high" | "critical"
    subject_key: str
    observation: str | None
    payload: dict[str, Any] = field(default_factory=dict)
    contributing_rows: list[EventLog] = field(default_factory=list)


@dataclass
class RootCauseReport:
    """Output of one investigator (tier-2 cross-source analysis).

    Triggered by one or more tier-1 Findings that the dispatcher grouped
    into a single incident (sharing correlation keys + time window).
    `trigger_finding_id` is the primary trigger (highest severity, earliest
    time); `trigger_finding_ids` carries the full set.

    `report_id` is populated by RootCauseReportEmitterSink AFTER the DB
    insert. Subsequent sinks in the same dispatch (e.g., ServiceNowReportSink)
    read it to UPDATE the persisted row with their own outputs (ticket
    number, etc.).
    """

    investigator_class: str
    trigger_finding_id: int
    trigger_finding_ids: list[int]
    severity: str
    summary: str
    evidence_chain: list[dict[str, Any]]  # ordered: root -> trail -> symptom
    related_event_ids: list[int]
    related_finding_ids: list[int]
    correlation_keys: dict[str, Any]
    payload: dict[str, Any] = field(default_factory=dict)

    # Populated mid-pipeline by sinks (see docstring)
    report_id: int | None = None
    servicenow_number: str | None = None
    servicenow_sys_id: str | None = None
    servicenow_url: str | None = None
