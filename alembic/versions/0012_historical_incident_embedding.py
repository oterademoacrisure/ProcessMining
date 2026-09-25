"""POINT 19 (Task #19): historical_incident.embedding (pgvector) + HNSW index

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-25

POINT 19: adds the pgvector column that the `pgvector` vector-store backend uses.
Each past incident's PROBLEM embedding is stored here (1536-d, matching Azure
text-embedding-3-small), with an HNSW index for fast cosine ANN search. Requires
the `vector` extension (enabled separately / on Neon it's available).

Co-located with the relational data, so the pgvector backend does ANN + tenant
filter in one SQL query. The FAISS backend ignores this column.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE historical_incident ADD COLUMN IF NOT EXISTS embedding vector(1536)")
    # HNSW index for cosine distance (<=>). Builds incrementally as rows get embeddings.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_historical_incident_embedding "
        "ON historical_incident USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_historical_incident_embedding")
    op.execute("ALTER TABLE historical_incident DROP COLUMN IF EXISTS embedding")
