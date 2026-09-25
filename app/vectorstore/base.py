"""POINT 19 (Task #19): the shared vector-store interface.

A `VectorStore` holds precedent EMBEDDINGS, each labeled with the
historical_incident.incident_id it came from, and answers "which PAST incidents
are most similar to this query vector?". The full precedent data (text, system,
dates, approved/rejected) stays in Postgres — the store is purely the similarity
index, and the incident_id is the link back to the Postgres row.

Note: only the STORED past incidents are keyed by incident_id. The new/current
incident is just a throwaway query vector (no id) that we search WITH.

Backends implement this interface; the active one is chosen in modules.yaml
(`vector_backend:`). Swapping faiss <-> pgvector <-> qdrant changes no other code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class VectorRecord:
    """One PAST incident to index: its id, its embedding, light filter metadata."""
    id: int                                   # historical_incident.incident_id
    vector: list[float]
    metadata: dict[str, Any] = field(default_factory=dict)   # e.g. {"tenant_id": 1}


@dataclass
class ScoredHit:
    """A search result: matched past-incident id + cosine similarity (0..1) + metadata."""
    id: int
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(ABC):
    """Swappable similarity index. Backends: faiss (default), pgvector, qdrant, ..."""

    @abstractmethod
    def upsert(self, records: list[VectorRecord]) -> None:
        """Add/replace records (idempotent by id)."""

    @abstractmethod
    def search(
        self, vector: list[float], *, k: int = 10, tenant_id: int | None = None
    ) -> list[ScoredHit]:
        """Return up to `k` most-similar PAST incident ids, optionally tenant-scoped."""

    @abstractmethod
    def count(self) -> int:
        """How many vectors are indexed."""

    @abstractmethod
    def clear(self) -> None:
        """Drop the whole index (used before a full rebuild)."""
