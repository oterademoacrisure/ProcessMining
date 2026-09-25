from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EventLog, Finding as FindingRow
from app.db.session import SessionLocal
from app.findings import Finding
from app.sinks.base import BaseSink


log = logging.getLogger(__name__)


class FindingsEmitterSink(BaseSink):
    """Persists analyzer Findings to the `finding` table.

    Behavior:
      - Skips findings with severity == 'normal' (noise, not actionable).
      - Dedup: skips a finding if an identical correlation_hash was inserted
        within the last `dedup_window_minutes` (default 10).
      - Correlation keys (server_ids, case_ids, actor_ids, time_min, time_max)
        are computed from each finding's `contributing_rows` so investigators
        can later query EVENT_LOG on those keys.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.persist_normal = bool(self.config.get("persist_normal", False))
        self.dedup_window_minutes = int(self.config.get("dedup_window_minutes", 10))
        self.time_bucket_minutes = int(self.config.get("time_bucket_minutes", 5))

    def handle(
        self,
        source_type: str,
        rows: Sequence[EventLog],
        result: Any,
        analyzer_class: str | None = None,
    ) -> None:
        if not isinstance(result, list):
            return
        findings: list[Finding] = [f for f in result if isinstance(f, Finding)]
        if not findings:
            return

        analyzer_class = analyzer_class or "unknown"
        tenant_id = self._infer_tenant_id(rows, findings)
        if tenant_id is None:
            log.warning("findings_emitter: no tenant_id resolvable, skipping %d finding(s)", len(findings))
            return

        persisted = 0
        skipped_normal = 0
        skipped_dedup = 0

        with SessionLocal() as session:
            for f in findings:
                if not self.persist_normal and f.severity == "normal":
                    skipped_normal += 1
                    continue
                if self._persist_one(session, f, source_type, analyzer_class, tenant_id):
                    persisted += 1
                else:
                    skipped_dedup += 1
            session.commit()

        log.info(
            "findings_emitter[%s]: persisted=%d, skipped_normal=%d, skipped_dedup=%d",
            source_type, persisted, skipped_normal, skipped_dedup,
        )

    def _infer_tenant_id(self, rows: Sequence[EventLog], findings: list[Finding]) -> int | None:
        if rows:
            return rows[0].tenant_id
        for f in findings:
            if f.contributing_rows:
                return f.contributing_rows[0].tenant_id
        return None

    def _persist_one(
        self,
        session: Session,
        finding: Finding,
        source_type: str,
        analyzer_class: str,
        tenant_id: int,
    ) -> bool:
        server_ids = sorted({r.server_id for r in finding.contributing_rows if r.server_id})
        case_ids = sorted({r.case_id for r in finding.contributing_rows if r.case_id is not None})
        actor_ids = sorted({r.actor_id for r in finding.contributing_rows if r.actor_id})
        event_ids = [r.event_id for r in finding.contributing_rows if r.event_id is not None]
        timestamps = [r.timestamp for r in finding.contributing_rows if r.timestamp]
        time_min = min(timestamps) if timestamps else None
        time_max = max(timestamps) if timestamps else None

        bucket = self._time_bucket(time_max or datetime.now(timezone.utc))
        correlation_hash = self._hash(tenant_id, source_type, finding.subject_key, finding.severity, bucket)

        if self._dedup_hit(session, correlation_hash):
            return False

        row = FindingRow(
            tenant_id=tenant_id,
            source_type=source_type,
            analyzer_class=analyzer_class,
            severity=finding.severity,
            subject_key=finding.subject_key,
            observation=finding.observation,
            payload=_jsonify(finding.payload),
            server_ids={"values": server_ids} if server_ids else None,
            case_ids={"values": case_ids} if case_ids else None,
            actor_ids={"values": actor_ids} if actor_ids else None,
            contributing_event_ids={"values": event_ids} if event_ids else None,
            time_min=time_min,
            time_max=time_max,
            correlation_hash=correlation_hash,
        )
        session.add(row)
        return True

    def _time_bucket(self, dt: datetime) -> str:
        bucket_min = (dt.minute // self.time_bucket_minutes) * self.time_bucket_minutes
        return dt.strftime("%Y%m%d%H") + f"{bucket_min:02d}"

    def _hash(self, tenant_id: int, source_type: str, subject_key: str, severity: str, bucket: str) -> str:
        h = hashlib.md5()
        h.update(f"{tenant_id}|{source_type}|{subject_key}|{severity}|{bucket}".encode("utf-8"))
        return h.hexdigest()

    def _dedup_hit(self, session: Session, correlation_hash: str) -> bool:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=self.dedup_window_minutes)
        existing = session.scalar(
            select(FindingRow.finding_id)
            .where(FindingRow.correlation_hash == correlation_hash)
            .where(FindingRow.produced_at > cutoff)
            .limit(1)
        )
        return existing is not None


def _jsonify(value: Any) -> Any:
    """Make sure the payload is JSON-serializable (datetime, etc.)."""
    if isinstance(value, dict):
        return {k: _jsonify(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonify(v) for v in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
