"""POINT 19 (Task #19): pluggable vector-store package.

WHERE precedent embeddings live is swappable — FAISS (default, local, no infra),
pgvector (Postgres, for production), Qdrant (dedicated vector DB), or another
backend later. Only the `VectorStore` interface is shared; the backend is chosen
in modules.yaml (`vector_backend:`). Swapping backends changes no other code.
"""
from app.vectorstore.base import ScoredHit, VectorRecord, VectorStore
from app.vectorstore.embedder import Embedder
from app.vectorstore.factory import get_vector_store

__all__ = ["VectorStore", "VectorRecord", "ScoredHit", "Embedder", "get_vector_store"]
