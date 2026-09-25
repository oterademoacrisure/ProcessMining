"""POINT 19 (Task #19): load fetched ServiceNow incidents into historical_incident.

Takes the plain dicts produced by client.py and upserts them into the
historical_incident table. "Upsert" = insert new rows, update existing ones —
keyed on (tenant_id, sys_id) so re-running the sync never creates duplicates
(idempotent). The DB fills search_tsv automatically; we never write it.

Transaction note: this function does NOT commit — the caller (the sync script)
owns the transaction, so a whole sync batch commits or rolls back together.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import HistoricalIncident
from app.readers.timestamps import parse_iso_timestamp


log = logging.getLogger(__name__)


# POINT 19: column length caps mirroring the table, so an unexpectedly long
# ServiceNow value can never blow up the insert.
_MAXLEN = {
    "number": 64, "sys_id": 64, "category": 128, "subcategory": 128,
    "cmdb_ci": 255, "priority": 16, "state": 32, "correlation_id": 100,
}

# POINT 19: the columns we write on a conflict (everything except identity keys
# and the DB-generated search_tsv). On re-sync these get refreshed.
_UPDATE_COLS = (
    "number", "short_description", "description", "close_notes", "category",
    "subcategory", "cmdb_ci", "priority", "state", "outcome", "opened_at", "resolved_at",
    "sys_updated_on", "correlation_id", "source_system", "raw_json",
)


def derive_outcome(close_code: Any, state: Any = None) -> str:
    """POINT 19: map a ServiceNow close_code -> approved | rejected | unknown.

    Handles BOTH older close codes ('Solved (Permanently)', 'Not Solved …') and the
    newer instance defaults ('Solution provided', 'Workaround provided', 'Resolved by …',
    'No resolution provided', 'Duplicate', 'Known error', 'User error').

    Resolved/fixed -> approved · explicitly not fixed -> rejected · everything else
    (duplicate / known error / user error / missing) -> unknown.
    """
    cc = str(close_code or "").strip().lower()
    if not cc:
        return "unknown"
    if (cc.startswith("solved") or cc.startswith("solution")
            or cc.startswith("workaround") or cc.startswith("resolved by")):
        return "approved"
    if cc.startswith("not solved") or cc == "no resolution provided":
        return "rejected"
    return "unknown"   # duplicate / known error / user error / etc.


def load_incidents(
    session: Session, tenant_id: int, records: list[dict[str, Any]]
) -> dict[str, int]:
    """Upsert ServiceNow records into historical_incident.

    Returns {"inserted": N, "updated": M, "skipped": K}.
    Does not commit — caller commits.
    """
    rows: list[dict[str, Any]] = []
    skipped = 0
    for r in records:
        sys_id = _trunc("sys_id", r.get("sys_id"))
        if not sys_id:
            # POINT 19: sys_id is our dedup key — a record without one can't be
            # safely upserted, so we skip (and count) rather than risk a dup.
            skipped += 1
            continue
        rows.append({
            "tenant_id":         tenant_id,
            "number":            _trunc("number", r.get("number")),
            "sys_id":            sys_id,
            "short_description": r.get("short_description"),
            "description":       r.get("description"),
            "close_notes":       r.get("close_notes"),
            "category":          _trunc("category", r.get("category")),
            "subcategory":       _trunc("subcategory", r.get("subcategory")),
            "cmdb_ci":           _trunc("cmdb_ci", r.get("cmdb_ci")),
            "priority":          _trunc("priority", r.get("priority")),
            "state":             _trunc("state", r.get("state")),
            # POINT 19: explicit `outcome` wins (seed / HITL); else derive from close_code.
            "outcome":           r.get("outcome") or derive_outcome(r.get("close_code"), r.get("state")),
            "opened_at":         _parse_dt(r.get("opened_at")),
            "resolved_at":       _parse_dt(r.get("resolved_at")),
            "sys_updated_on":    _parse_dt(r.get("sys_updated_on")),
            "correlation_id":    _trunc("correlation_id", r.get("correlation_id")),
            "source_system":     "servicenow",
            "raw_json":          r.get("_raw"),
        })

    if not rows:
        return {"inserted": 0, "updated": 0, "skipped": skipped}

    # POINT 19: figure out insert-vs-update counts BEFORE upserting — which of
    # these sys_ids already exist for this tenant?
    batch_sys_ids = {row["sys_id"] for row in rows}
    existing = set(session.scalars(
        select(HistoricalIncident.sys_id)
        .where(HistoricalIncident.tenant_id == tenant_id)
        .where(HistoricalIncident.sys_id.in_(batch_sys_ids))
    ).all())
    updated = len(batch_sys_ids & existing)
    inserted = len(batch_sys_ids) - updated

    # POINT 19: the actual upsert. ON CONFLICT (tenant_id, sys_id) DO UPDATE
    # refreshes the data columns + synced_at. One atomic statement for the batch.
    stmt = pg_insert(HistoricalIncident).values(rows)
    set_ = {col: stmt.excluded[col] for col in _UPDATE_COLS}
    set_["synced_at"] = func.now()
    stmt = stmt.on_conflict_do_update(
        index_elements=["tenant_id", "sys_id"],
        set_=set_,
    )
    session.execute(stmt)

    log.info(
        "load_incidents[tenant=%d]: inserted=%d updated=%d skipped=%d",
        tenant_id, inserted, updated, skipped,
    )
    return {"inserted": inserted, "updated": updated, "skipped": skipped}


# ── helpers ─────────────────────────────────────────────────────────────────

def _trunc(field: str, value: Any) -> str | None:
    if value in (None, ""):
        return None
    s = str(value)
    cap = _MAXLEN.get(field)
    return s[:cap] if cap else s


def _parse_dt(value: Any) -> datetime | None:
    """ServiceNow gives 'YYYY-MM-DD HH:MM:SS' (UTC). Parse tolerantly; None on failure."""
    if not value:
        return None
    try:
        return parse_iso_timestamp(str(value).replace(" ", "T"))
    except (ValueError, TypeError):
        return None
