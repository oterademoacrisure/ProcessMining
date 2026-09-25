"""POINT 19 (Task #19): FAISS vector-store backend — the DEFAULT.

In-process FAISS index, persisted to a local file (no server, no infra). Holds
precedent embeddings keyed by historical_incident.incident_id, plus light
metadata (tenant_id) for scoped search. Uses exact cosine similarity
(inner-product on L2-normalized vectors) — exact and fast at our scale (a few
thousand incidents). HNSW can swap in later for very large corpora.

Persistence: <path> (the FAISS index) + <path>.meta.json (the id->metadata map).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from app.vectorstore.base import ScoredHit, VectorRecord, VectorStore

log = logging.getLogger(__name__)


class FaissStore(VectorStore):
    def __init__(self, path: str = ".data/precedent.faiss"):
        self.path = Path(path)
        self.meta_path = self.path.with_suffix(self.path.suffix + ".meta.json")
        self._index = None                       # faiss.IndexIDMap (lazy)
        self._meta: dict[int, dict] = {}          # incident_id -> metadata
        self._load()

    # --- lifecycle ---
    def _load(self) -> None:
        if self.path.exists() and self.meta_path.exists():
            import faiss
            self._index = faiss.read_index(str(self.path))
            raw = json.loads(self.meta_path.read_text(encoding="utf-8"))
            self._meta = {int(k): v for k, v in raw.items()}
            log.info("FaissStore: loaded %d vectors from %s", self._index.ntotal, self.path)

    def _new_index(self, dim: int) -> None:
        import faiss
        # IndexIDMap lets us add/search by our own incident_id (not 0..n-1).
        self._index = faiss.IndexIDMap(faiss.IndexFlatIP(dim))

    def _persist(self) -> None:
        import faiss
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._index is not None:
            faiss.write_index(self._index, str(self.path))
            self.meta_path.write_text(json.dumps(self._meta), encoding="utf-8")

    @staticmethod
    def _normalize(arr: np.ndarray) -> np.ndarray:
        import faiss
        arr = np.ascontiguousarray(arr, dtype="float32")
        faiss.normalize_L2(arr)                  # so inner-product == cosine similarity
        return arr

    # --- VectorStore API ---
    def upsert(self, records: list[VectorRecord]) -> None:
        if not records:
            return
        if self._index is None:
            self._new_index(len(records[0].vector))
        ids = np.array([r.id for r in records], dtype="int64")
        try:
            self._index.remove_ids(ids)          # idempotent: drop existing before re-adding
        except Exception:
            pass
        vecs = self._normalize(np.array([r.vector for r in records], dtype="float32"))
        self._index.add_with_ids(vecs, ids)
        for r in records:
            self._meta[int(r.id)] = dict(r.metadata or {})
        self._persist()

    def search(self, vector, *, k: int = 10, tenant_id: int | None = None) -> list[ScoredHit]:
        if self._index is None or self._index.ntotal == 0:
            return []
        q = self._normalize(np.array([vector], dtype="float32"))
        # Over-fetch when filtering by tenant, since filtering happens after the ANN search.
        pool = min((k * 5 if tenant_id is not None else k), self._index.ntotal)
        scores, ids = self._index.search(q, pool)
        hits: list[ScoredHit] = []
        for score, _id in zip(scores[0], ids[0]):
            if _id == -1:
                continue
            meta = self._meta.get(int(_id), {})
            if tenant_id is not None and meta.get("tenant_id") != tenant_id:
                continue
            hits.append(ScoredHit(id=int(_id), score=float(score), metadata=meta))
            if len(hits) >= k:
                break
        return hits

    def count(self) -> int:
        return 0 if self._index is None else int(self._index.ntotal)

    def clear(self) -> None:
        self._index = None
        self._meta = {}
        for p in (self.path, self.meta_path):
            try:
                p.unlink()
            except FileNotFoundError:
                pass
