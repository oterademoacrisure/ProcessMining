"""Correlator — turn raw Mule events into RCA evidence the analyzer can cite.

Ported from mule-rca/src/correlator.py. The whole reason Mule logs are worth
ingesting is that they *deterministically* distinguish:

  - **downstream system failed** -> page the downstream owner, not Mule
  - **Mule itself failed** -> investigate Mule, not the downstream

The §8 outcome-classification rules MUST be implemented exactly, in the
documented order. Do not soften the summary language either — Mule's HTTP
connector behavior is deterministic; if it logged SocketTimeoutException,
downstream IS the cause.

Note: the standalone module's `enrich_incident_with_mule()` is intentionally
NOT ported. It assumed an enrich-an-Incident model serverops does not have;
here the reusable assets are `group_into_executions` + `_classify_outcome` +
`_build_summary`, wrapped by app.analyzers.mule_analyzer.MuleAnalyzer.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from app.mule.schemas import MuleIntegrationEvent


Outcome = Literal[
    "success",
    "downstream_timeout",       # SocketTimeoutException — downstream slow/dead
    "downstream_error",         # HTTP 5xx from downstream
    "downstream_unreachable",   # ConnectionException — no connection at all
    "mule_internal_error",      # flow.error before any outbound http.request
    "unknown_error",
]

# Subset of outcomes that mean a downstream system is implicated.
DOWNSTREAM_FAULT: frozenset[Outcome] = frozenset({
    "downstream_timeout",
    "downstream_error",
    "downstream_unreachable",
})


# === Value objects ==========================================================


@dataclass
class MuleFlowExecution:
    """One flow execution — a contiguous group of events sharing a correlation_id."""

    correlation_id: str
    appian_process_id: str | None
    user_uuid: str | None
    flow_name: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    downstream_system: str | None
    outcome: Outcome
    error_type: str | None
    error_message: str | None
    events: list[MuleIntegrationEvent]

    @property
    def is_failure(self) -> bool:
        return self.outcome != "success"


# === Outcome classification (§8 rules — order matters) ======================


def _classify_outcome(sorted_events: list[MuleIntegrationEvent]) -> Outcome:
    """Apply the §8 rules in exact order. ``sorted_events`` must be ts-sorted ascending."""

    flow_error = next((e for e in sorted_events if e.event_type == "flow.error"), None)
    first_http_request_ts = next(
        (e.timestamp for e in sorted_events if e.event_type == "http.request"),
        None,
    )
    http_error = next((e for e in sorted_events if e.event_type == "http.error"), None)
    http_response_5xx = next(
        (e for e in sorted_events
         if e.event_type == "http.response"
         and e.http_status is not None
         and e.http_status >= 500),
        None,
    )

    # Rule 1 — flow.error AND no http.request preceded it -> Mule itself failed
    if flow_error is not None:
        no_preceding_request = (
            first_http_request_ts is None
            or first_http_request_ts >= flow_error.timestamp
        )
        if no_preceding_request:
            return "mule_internal_error"

    # Rules 2-5 — classify by the http.error's error_type
    if http_error is not None:
        et = http_error.error_type or ""
        if "SocketTimeoutException" in et:
            return "downstream_timeout"
        if "ConnectionException" in et or "ConnectException" in et:
            return "downstream_unreachable"
        if "HttpResponseException" in et:
            return "downstream_error"
        return "unknown_error"  # rule 5

    # Rule 6 — http.response with status >= 500
    if http_response_5xx is not None:
        return "downstream_error"

    # Rule 7 — flow.error exists but didn't match rule 1
    if flow_error is not None:
        return "unknown_error"

    # Rule 8 — success
    return "success"


def _build_execution(group: list[MuleIntegrationEvent]) -> MuleFlowExecution | None:
    """Build one MuleFlowExecution from a group of events with the same correlation_id.

    Returns None if the group has no ``flow.start`` (ambient heartbeats, lone
    log entries, recovery markers — they're not executions and the spec says
    to skip them).
    """
    sorted_events = sorted(group, key=lambda e: e.timestamp)
    flow_start = next((e for e in sorted_events if e.event_type == "flow.start"), None)
    if flow_start is None:
        return None

    outcome = _classify_outcome(sorted_events)

    end_event = next(
        (e for e in sorted_events if e.event_type in ("flow.end", "flow.error")),
        None,
    )
    ended_at = end_event.timestamp if end_event else None
    duration_ms = end_event.duration_ms if end_event else None

    # Error context: prefer the http.error's diagnostic over the flow.error's
    # because http.error carries the underlying exception class/message.
    error_carrier = next(
        (e for e in sorted_events
         if e.event_type == "http.error" and e.error_type is not None),
        None,
    )
    if error_carrier is None:
        error_carrier = next(
            (e for e in sorted_events
             if e.event_type == "flow.error" and e.error_type is not None),
            None,
        )
    error_type = error_carrier.error_type if error_carrier else None
    error_message = error_carrier.error_message if error_carrier else None

    return MuleFlowExecution(
        correlation_id=flow_start.correlation_id,
        appian_process_id=flow_start.appian_process_id,
        user_uuid=flow_start.user_uuid,
        flow_name=flow_start.flow_name,
        started_at=flow_start.timestamp,
        ended_at=ended_at,
        duration_ms=duration_ms,
        downstream_system=flow_start.downstream_system,
        outcome=outcome,
        error_type=error_type,
        error_message=error_message,
        events=sorted_events,
    )


def group_into_executions(events: list[MuleIntegrationEvent]) -> list[MuleFlowExecution]:
    """Collapse a flat event list into per-execution rollups.

    Groups by ``correlation_id``, sorts each group by timestamp, classifies
    the outcome via the §8 rules. Groups without a ``flow.start`` (ambient
    DEBUG heartbeats, the recovery marker) are dropped — they are not
    executions.

    Returns the list of executions sorted by ``(started_at, correlation_id)``
    so output is deterministic.
    """
    by_corr: dict[str, list[MuleIntegrationEvent]] = {}
    for e in events:
        by_corr.setdefault(e.correlation_id, []).append(e)
    executions: list[MuleFlowExecution] = []
    for group in by_corr.values():
        execution = _build_execution(group)
        if execution is not None:
            executions.append(execution)
    executions.sort(key=lambda x: (x.started_at, x.correlation_id))
    return executions


# === Summary phrasing =======================================================


def build_summary(
    failed: list[MuleFlowExecution],
    implicated_downstream: list[str],
) -> str:
    """One-sentence localization the analyzer can quote verbatim.

    NO hedging. The §8 rules turn deterministic Mule signals into deterministic
    outcomes — if SocketTimeoutException was logged, downstream IS the cause.
    """
    if not failed:
        return "No failed Mule flow executions in window. Mule layer is healthy."

    n = len(failed)
    counts = Counter(x.outcome for x in failed)
    timeout = counts.get("downstream_timeout", 0)
    err5xx = counts.get("downstream_error", 0)
    unreach = counts.get("downstream_unreachable", 0)
    internal = counts.get("mule_internal_error", 0)
    unknown = counts.get("unknown_error", 0)

    parts: list[str] = []
    if timeout:
        parts.append(
            f"{timeout} timed out waiting for downstream "
            f"(SocketTimeoutException from Mule's HTTP connector)"
        )
    if err5xx:
        parts.append(f"{err5xx} received HTTP 5xx from downstream")
    if unreach:
        parts.append(f"{unreach} could not connect to downstream")
    if internal:
        parts.append(f"{internal} failed inside Mule before any outbound HTTP")
    if unknown:
        parts.append(f"{unknown} failed with unclassified errors")

    head = f"Of {n} failed Mule flow executions in window: {'; '.join(parts)}."

    downstream_implicated = bool(implicated_downstream)
    mule_implicated = internal > 0

    if downstream_implicated and not mule_implicated:
        tail = (
            f" The failure localizes to downstream system(s): "
            f"{', '.join(implicated_downstream)}."
        )
    elif mule_implicated and not downstream_implicated:
        tail = " Mule runtime is implicated — investigate Mule itself."
    elif downstream_implicated and mule_implicated:
        tail = (
            f" Both downstream system(s) ({', '.join(implicated_downstream)}) "
            f"and Mule itself are implicated — investigate both layers."
        )
    else:
        tail = " Unable to localize — failures are unclassified."

    return head + tail
