"""POINT 19 (Task #19): pick the active vector backend from config.

modules.yaml -> `vector_backend: faiss | pgvector | qdrant` (default faiss).
Backends are imported lazily, so unused client libs aren't required.
"""
from __future__ import annotations

import logging

from app.vectorstore.base import VectorStore

log = logging.getLogger(__name__)


def get_vector_store(config: dict | None = None) -> VectorStore:
    config = config or {}
    backend = str(config.get("vector_backend", "faiss")).lower()

    if backend == "faiss":
        from app.vectorstore.faiss_store import FaissStore
        store = FaissStore(path=config.get("vector_index_path", ".data/precedent.faiss"))
    elif backend == "pgvector":
        from app.vectorstore.pgvector_store import PgVectorStore
        store = PgVectorStore(config)
    elif backend == "qdrant":
        from app.vectorstore.qdrant_store import QdrantStore
        store = QdrantStore(config)
    else:
        raise ValueError(
            f"unknown vector_backend '{backend}' (expected: faiss | pgvector | qdrant)"
        )

    log.info("Vector backend: %s", backend)
    return store
