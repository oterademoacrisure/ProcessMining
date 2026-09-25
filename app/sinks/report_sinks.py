from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from app.db.models import RootCauseReport as RootCauseReportRow
from app.db.session import SessionLocal
from app.findings import RootCauseReport


log = logging.getLogger(__name__)


class BaseReportSink(ABC):
    """Consumes a RootCauseReport produced by an investigator.

    Separate from BaseSink because root-cause reports have a different
    shape than per-source analyzer findings.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def handle(self, report: RootCauseReport) -> None:
        ...


class ConsoleReportSink(BaseReportSink):
    """Pretty-prints a RootCauseReport to stdout."""

    def handle(self, report: RootCauseReport) -> None:
        triggers_str = ",".join(str(t) for t in report.trigger_finding_ids) or str(report.trigger_finding_id)
        print(f"\n[INVESTIGATOR] {report.investigator_class} severity={report.severity}")
        print(f"  incident: {len(report.trigger_finding_ids)} finding(s) -> [{triggers_str}]  primary={report.trigger_finding_id}")
        print(f"  summary: {report.summary}")
        print(f"  related_event_count={len(report.related_event_ids)}  "
              f"related_finding_count={len(report.related_finding_ids)}")
        print("  evidence_chain:")
        for i, e in enumerate(report.evidence_chain, 1):
            marker = " *" if e.get("is_trigger") else "  "
            tier        = e.get("tier")        or e.get("role")   or ""
            source      = e.get("source_type") or e.get("source") or ""
            severity    = e.get("severity")    or ""
            subject     = e.get("subject")     or e.get("signal") or ""
            observation = e.get("observation") or e.get("signal") or ""
            print(f"   {marker}{i}. [{tier:<18}] {source:<28} "
                  f"sev={severity:<8} subject={subject}")
            if observation and observation != subject:
                print(f"        \"{observation}\"")
        try:
            print("  correlation_keys:")
            print("    " + json.dumps(report.correlation_keys, indent=2, default=str).replace("\n", "\n    "))
        except (TypeError, ValueError):
            pass


class RootCauseReportEmitterSink(BaseReportSink):
    """Persists a RootCauseReport to the `root_cause_report` table.

    No dedup logic: each investigation produces at most one report (the
    investigator dispatcher already prevents re-investigation via
    `finding.processed_at`), so there's no race to guard against here.
    """

    def handle(self, report: RootCauseReport) -> None:
        tenant_id = self._infer_tenant_id(report)
        if tenant_id is None:
            log.warning(
                "root_cause_report_emitter: no tenant_id resolvable for trigger_finding_id=%d",
                report.trigger_finding_id,
            )
            return

        row = RootCauseReportRow(
            tenant_id=tenant_id,
            investigator_class=report.investigator_class,
            trigger_finding_id=report.trigger_finding_id,
            trigger_finding_ids=(
                {"values": list(report.trigger_finding_ids)}
                if report.trigger_finding_ids else None
            ),
            severity=report.severity,
            summary=report.summary,
            evidence_chain=_jsonify({"entries": report.evidence_chain}),
            related_event_ids={"values": list(report.related_event_ids)} if report.related_event_ids else None,
            related_finding_ids={"values": list(report.related_finding_ids)} if report.related_finding_ids else None,
            correlation_keys=_jsonify(report.correlation_keys),
            payload=_jsonify(report.payload),
        )
        with SessionLocal() as session:
            session.add(row)
            session.commit()
            # After commit, row.report_id is populated (autoincrement PK).
            # Stamp it onto the dataclass so subsequent sinks in the same
            # dispatch (e.g., ServiceNowReportSink) can UPDATE this row.
            report.report_id = row.report_id
        log.info(
            "root_cause_report_emitter: persisted report_id=%d for trigger_finding_id=%d severity=%s",
            row.report_id, report.trigger_finding_id, report.severity,
        )

    def _infer_tenant_id(self, report: RootCauseReport) -> int | None:
        keys = report.correlation_keys or {}
        tenant_id = keys.get("tenant_id")
        if isinstance(tenant_id, int):
            return tenant_id
        return None


def _jsonify(value: Any) -> Any:
    """Recursively coerce datetimes to ISO strings so JSONB writes don't fail."""
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
