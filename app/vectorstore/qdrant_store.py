"""POINT 19 (Task #19): Qdrant backend — PLACEHOLDER (not yet implemented).

A dedicated vector database. Good if the corpus outgrows pgvector (10M+ vectors)
or you want managed vector search with native payload filtering.

To implement later (needs `qdrant-client`, lazy-imported here so FAISS users
don't need it):
  - __init__: QdrantClient(url=..., api_key=...); ensure a collection exists.
  - upsert():  client.upsert(collection, points=[{id, vector, payload={tenant_id,...}}])
  - search():  client.search(collection, query_vector, limit=k,
                             query_filter=Filter(tenant_id == :tenant))
Until built, select `vector_backend: faiss` in modules.yaml.
"""
from __future__ import annotations

from app.vectorstore.base import ScoredHit, VectorRecord, VectorStore


class QdrantStore(VectorStore):
    def __init__(self, config: dict | None = None):
        raise NotImplementedError(
            "qdrant backend is not implemented yet. Set `vector_backend: faiss` in "
            "modules.yaml, or implement QdrantStore (see docs/VECTOR_RETRIEVAL_DESIGN.md)."
        )

    def upsert(self, records: list[VectorRecord]) -> None:
        raise NotImplementedError

    def search(self, vector, *, k: int = 10, tenant_id: int | None = None) -> list[ScoredHit]:
        raise NotImplementedError

    def count(self) -> int:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError
