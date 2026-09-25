from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db.models import EventLog


def poll_unprocessed(
    session: Session,
    tenant_ids: Sequence[int],
    batch_size: int = 500,
) -> list[EventLog]:
    """Fetch up to `batch_size` unprocessed EVENT_LOG rows for the given tenants,
    oldest first. This is the shared, source-agnostic poll the orchestrator
    runs on each cycle."""
    if not tenant_ids:
        return []
    stmt = (
        select(EventLog)
        .where(EventLog.tenant_id.in_(tenant_ids))
        .where(EventLog.processed_at.is_(None))
        .order_by(EventLog.timestamp.asc())
        .limit(batch_size)
    )
    return list(session.scalars(stmt).all())


def mark_processed(session: Session, rows: Sequence[EventLog]) -> None:
    """Stamp `processed_at = now()` on the given rows so the next poll skips them."""
    if not rows:
        return
    ids = [r.event_id for r in rows]
    now = datetime.now(timezone.utc)
    session.execute(
        update(EventLog).where(EventLog.event_id.in_(ids)).values(processed_at=now)
    )
    session.commit()
