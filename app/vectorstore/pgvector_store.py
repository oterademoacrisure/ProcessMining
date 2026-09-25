"""POINT 19 (Task #19): pgvector backend.

Stores each past incident's PROBLEM embedding in the `embedding vector(N)` column on
historical_incident (migration 0012) and does ANN + tenant filter in ONE SQL query
using cosine distance (`<=>`). Co-located with the relational data: no separate index
file, no sync. Requires the pgvector extension + the embedding column.

Vectors are passed as text literals cast to `vector` (e.g. '[0.1,0.2,...]'::vector),
so no extra Python driver/dependency is needed.
"""
from __future__ import annotations

import logging

from sqlalchemy import text

from app.db.session import SessionLocal
from app.vectorstore.base import ScoredHit, VectorRecord, VectorStore

log = logging.getLogger(__name__)


def _vec_literal(vector) -> str:
    """Format a vector as a pgvector text literal: [v1,v2,...]."""
    return "[" + ",".join(str(float(x)) for x in vector) + "]"


class PgVectorStore(VectorStore):
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def upsert(self, records: list[VectorRecord]) -> None:
        # The incident row already exists (the indexer reads it) — set its embedding.
        if not records:
            return
        with SessionLocal() as s:
            for r in records:
                s.execute(
                    text("UPDATE historical_incident SET embedding = CAST(:vec AS vector) "
                         "WHERE incident_id = :id"),
                    {"vec": _vec_literal(r.vector), "id": int(r.id)},
                )
            s.commit()

    def search(self, vector, *, k: int = 10, tenant_id: int | None = None) -> list[ScoredHit]:
        where = ["embedding IS NOT NULL"]
        params = {"q": _vec_literal(vector), "k": int(k)}
        if tenant_id is not None:
            where.append("tenant_id = :tenant")
            params["tenant"] = tenant_id
        sql = (
            "SELECT incident_id, tenant_id, 1 - (embedding <=> CAST(:q AS vector)) AS score "
            "FROM historical_incident WHERE " + " AND ".join(where) +
            " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :k"
        )
        with SessionLocal() as s:
            rows = s.execute(text(sql), params).mappings().all()
        return [
            ScoredHit(id=int(r["incident_id"]), score=float(r["score"]),
                      metadata={"tenant_id": r["tenant_id"]})
            for r in rows
        ]

    def count(self) -> int:
        with SessionLocal() as s:
            return int(s.execute(
                text("SELECT count(*) FROM historical_incident WHERE embedding IS NOT NULL")
            ).scalar() or 0)

    def clear(self) -> None:
        with SessionLocal() as s:
            s.execute(text("UPDATE historical_incident SET embedding = NULL"))
            s.commit()
