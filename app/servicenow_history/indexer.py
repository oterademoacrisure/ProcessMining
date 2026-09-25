"""POINT 19 (Task #19): build the FAISS precedent index from historical_incident.

Reads PAST incidents from Postgres, embeds each one's PROBLEM text
(short_description + description — NOT the resolution), and loads them into the
active vector store, keyed by incident_id. Full-rebuild semantics: clear then add.

The resolution (close_notes), outcome (approved/rejected), dates and other fields
stay in Postgres and are looked up by id at retrieval time — only the problem
embedding lives in the store.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import HistoricalIncident
from app.vectorstore import Embedder, VectorRecord, VectorStore

log = logging.getLogger(__name__)


def problem_text(h: HistoricalIncident) -> str:
    """The PROBLEM we match on (symptom) — short_description + description.
    Deliberately excludes close_notes (the resolution is payload, not match key)."""
    return " ".join(p for p in (h.short_description, h.description) if p).strip()


def build_index(
    session: Session,
    store: VectorStore,
    embedder: Embedder,
    *,
    tenant_id: int | None = None,
    batch_size: int = 200,
) -> int:
    """(Re)build the vector index from historical_incident. Returns #indexed."""
    stmt = select(HistoricalIncident)
    if tenant_id is not None:
        stmt = stmt.where(HistoricalIncident.tenant_id == tenant_id)
    rows = [h for h in session.scalars(stmt).all() if problem_text(h)]

    # Always start clean — prevents stale entries when the table shrank/emptied
    # (e.g. after pointing DATABASE_URL at a different/empty database).
    store.clear()
    if not rows:
        log.info("build_index: no historical incidents with problem text to index")
        return 0

    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        vectors = embedder.embed([problem_text(h) for h in chunk])
        store.upsert([
            VectorRecord(id=h.incident_id, vector=v, metadata={"tenant_id": h.tenant_id})
            for h, v in zip(chunk, vectors)
        ])

    log.info("build_index: indexed %d incidents (store count=%d)", len(rows), store.count())
    return len(rows)
