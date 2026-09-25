from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy.orm import Session

from app.db.models import Finding as FindingRow
from app.orchestrator.findings_reader import poll_unprocessed_findings, mark_findings_processed
from app.orchestrator.incident_grouper import Incident, group_findings_into_incidents
from app.orchestrator.registry import Registry
from app.observability import progress  # POINT 21 (Task #21): live stage events


log = logging.getLogger(__name__)


class InvestigatorDispatcher:
    """Tier-2 dispatcher.

    Each cycle:
      1. Poll unprocessed Findings (severity >= 'high') for the configured tenants.
      2. Group findings into INCIDENTS by overlapping correlation keys + time window.
      3. For each incident, find investigators that match ANY of its findings'
         (source_type, severity); run each matching investigator ONCE with
         the whole incident; route the resulting RootCauseReport to each of
         the investigator's sinks.
      4. Mark every polled finding as processed regardless (don't retry-loop).
    """

    def __init__(
        self,
        registry: Registry,
        tenant_ids: Sequence[int],
        min_severity: str = "high",
        batch_size: int = 100,
    ):
        self.registry = registry
        self.tenant_ids = list(tenant_ids)
        self.min_severity = min_severity
        self.batch_size = batch_size

    def run_once(self, session: Session) -> int:
        findings = poll_unprocessed_findings(
            session, self.tenant_ids, self.min_severity, self.batch_size,
        )
        if not findings:
            return 0

        incidents = group_findings_into_incidents(findings)
        log.info(
            "investigator_dispatcher: %d finding(s) grouped into %d incident(s)",
            len(findings), len(incidents),
        )
        # POINT 21 (Task #21): CORRELATE telemetry — findings grouped into incidents.
        progress.emit("CORRELATE", "succeeded", tenant_id=findings[0].tenant_id,
                      step=f"Connected {len(findings)} related signal(s) into {len(incidents)} incident(s) across systems",
                      meta={"findings": len(findings), "incidents": len(incidents)})

        for incident in incidents:
            matching = self._matching_investigators(incident)
            if not matching:
                continue
            for inv_entry in matching:
                self._run_investigator(inv_entry, incident, session)

        mark_findings_processed(session, findings)
        return len(findings)

    def _matching_investigators(self, incident: Incident) -> list:
        """An investigator matches if ANY finding in the incident satisfies
        one of its triggers."""
        matched = []
        for inv_entry in self.registry.investigators():
            if any(
                inv_entry.matches(f.source_type, f.severity)
                for f in incident.findings
            ):
                matched.append(inv_entry)
        return matched

    def _run_investigator(self, inv_entry, incident: Incident, session: Session) -> None:
        try:
            cls = inv_entry.investigator_class()
            investigator = cls(config=inv_entry.config)
            report = investigator.investigate(incident, session)
        except Exception:
            log.exception(
                "Investigator %s failed on incident (primary_finding_id=%d)",
                inv_entry.name, incident.primary_finding.finding_id,
            )
            return

        if report is None:
            return

        for sink_cls in inv_entry.sink_classes():
            try:
                sink = sink_cls(config=inv_entry.config)
                sink.handle(report)
            except Exception:
                log.exception(
                    "Report sink %s failed for investigator %s",
                    sink_cls.__name__, inv_entry.name,
                )
