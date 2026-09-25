"""MuleAnalyzer: tier-1 analyzer for Mulesoft runtime events.

Rehydrates the `MuleIntegrationEvent`s the reader stored in `metadata_json`,
collapses them into per-execution rollups via the ported §8 correlation core,
and emits one `Finding` per implicated subject (a downstream system, or Mule
itself). The Finding's `contributing_rows` are the underlying EventLog rows —
they carry the `case_id` resolved from `appian_process_id`, which is what lets
the incident grouper merge this finding with the correlated Appian findings.

The deterministic localization sentence (build_summary) becomes the Finding's
`observation`, and the smoking-gun event_ids land in the payload — both feed
the LlmRcaInvestigator's prompt verbatim, collapsing "probable cause" into
"definitive cause".
"""

from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Sequence

from pydantic import ValidationError

from app.analyzers.base import BaseAnalyzer
from app.db.models import EventLog
from app.findings import Finding
from app.mule.correlation import (
    DOWNSTREAM_FAULT,
    MuleFlowExecution,
    build_summary,
    group_into_executions,
)
from app.mule.schemas import MuleIntegrationEvent


log = logging.getLogger(__name__)


DEFAULTS = {
    "critical_failure_count": 3,   # >= N failed executions for one subject => critical
    "high_failure_count": 1,       # >= N failed executions for one subject => high
}

_MULE_SUBJECT = "mule_runtime"


class MuleAnalyzer(BaseAnalyzer):
    """Per-subject analyzer for Mule runtime events.

    Groups failed flow executions by the system they implicate (a downstream
    system, or Mule itself for internal errors) and classifies severity by
    failure volume.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        merged = {**DEFAULTS, **(self.config or {})}
        self.critical_failure_count = int(merged["critical_failure_count"])
        self.high_failure_count = int(merged["high_failure_count"])

    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        if not rows:
            return []

        events: list[MuleIntegrationEvent] = []
        rows_by_corr: dict[str, list[EventLog]] = {}
        for r in rows:
            event = self._rehydrate(r)
            if event is None:
                continue
            events.append(event)
            rows_by_corr.setdefault(event.correlation_id, []).append(r)

        if not events:
            return []

        executions = group_into_executions(events)
        failed = [x for x in executions if x.is_failure]
        if not failed:
            return []

        # Bucket failed executions by the subject they implicate.
        by_subject: dict[str, list[MuleFlowExecution]] = {}
        for x in failed:
            by_subject.setdefault(self._subject_of(x), []).append(x)

        findings: list[Finding] = []
        for subject, subject_failures in by_subject.items():
            findings.append(
                self._build_finding(subject, subject_failures, rows_by_corr)
            )
        return findings

    # --- internals ------------------------------------------------------

    def _rehydrate(self, row: EventLog) -> MuleIntegrationEvent | None:
        meta = row.metadata_json
        if not isinstance(meta, dict):
            return None
        try:
            return MuleIntegrationEvent.model_validate(meta)
        except ValidationError:
            # Reader already validated on ingest; a failure here means the row
            # predates a schema change. Skip rather than crash the batch.
            log.warning("mule_analyzer: could not rehydrate event_id=%s", row.event_id)
            return None

    def _subject_of(self, execution: MuleFlowExecution) -> str:
        if execution.outcome == "mule_internal_error":
            return _MULE_SUBJECT
        if execution.outcome in DOWNSTREAM_FAULT and execution.downstream_system:
            return execution.downstream_system
        return execution.downstream_system or "unknown"

    def _build_finding(
        self,
        subject: str,
        subject_failures: list[MuleFlowExecution],
        rows_by_corr: dict[str, list[EventLog]],
    ) -> Finding:
        n = len(subject_failures)
        if n >= self.critical_failure_count:
            severity = "critical"
        elif n >= self.high_failure_count:
            severity = "high"
        else:
            severity = "normal"

        implicated = sorted({
            x.downstream_system
            for x in subject_failures
            if x.outcome in DOWNSTREAM_FAULT and x.downstream_system
        })
        observation = build_summary(subject_failures, implicated)

        # Smoking-gun event_ids the investigator can cite without hallucinating.
        smoking_guns: list[MuleIntegrationEvent] = []
        for x in subject_failures:
            smoking_guns.extend(
                e for e in x.events if e.event_type in ("http.error", "flow.error")
            )
        smoking_guns.sort(key=lambda e: (e.timestamp, str(e.event_id)))
        cited_event_ids = [str(e.event_id) for e in smoking_guns]

        # Contributing EventLog rows — these carry the case_id that drives the
        # cross-source incident merge.
        contributing_rows: list[EventLog] = []
        for x in subject_failures:
            contributing_rows.extend(rows_by_corr.get(x.correlation_id, []))

        affected_process_ids = sorted({
            x.appian_process_id for x in subject_failures if x.appian_process_id
        })

        payload: dict[str, Any] = {
            "subject": subject,
            "failed_execution_count": n,
            "outcome_breakdown": dict(Counter(x.outcome for x in subject_failures)),
            "downstream_systems_implicated": implicated,
            "affected_appian_process_ids": affected_process_ids,
            "cited_event_ids": cited_event_ids,
            "severity": severity,
            "observation": observation,
        }

        return Finding(
            severity=severity,
            subject_key=subject,
            observation=observation,
            payload=payload,
            contributing_rows=contributing_rows,
        )
