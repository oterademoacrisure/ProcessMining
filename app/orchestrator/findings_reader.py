from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import Finding as FindingRow


SEVERITY_RANK = {"normal": 0, "high": 1, "critical": 2}


def poll_unprocessed_findings(
    session: Session,
    tenant_ids: Sequence[int],
    min_severity: str = "high",
    batch_size: int = 100,
) -> list[FindingRow]:
    """Fetch up to `batch_size` unprocessed Findings whose severity is
    >= `min_severity`. Oldest first. Tenant-scoped."""
    if not tenant_ids:
        return []
    min_rank = SEVERITY_RANK.get(min_severity, 0)
    candidate_severities = [sev for sev, rank in SEVERITY_RANK.items() if rank >= min_rank]
    stmt = (
        select(FindingRow)
        .where(FindingRow.tenant_id.in_(tenant_ids))
        .where(FindingRow.processed_at.is_(None))
        .where(FindingRow.severity.in_(candidate_severities))
        .order_by(FindingRow.produced_at.asc())
        .limit(batch_size)
    )
    return list(session.scalars(stmt).all())


def mark_findings_processed(session: Session, findings: Sequence[FindingRow]) -> None:
    if not findings:
        return
    ids = [f.finding_id for f in findings]
    now = datetime.now(timezone.utc)
    session.execute(
        update(FindingRow)
        .where(FindingRow.finding_id.in_(ids))
        .values(processed_at=now)
    )
    session.commit()
