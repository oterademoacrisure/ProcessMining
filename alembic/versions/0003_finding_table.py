"""create finding table (tier-2 analyzer output, investigator input)

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "finding",
        sa.Column("finding_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("analyzer_class", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("subject_key", sa.String(length=255), nullable=False),
        sa.Column("observation", sa.Text(), nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("server_ids", JSONB, nullable=True),
        sa.Column("case_ids", JSONB, nullable=True),
        sa.Column("actor_ids", JSONB, nullable=True),
        sa.Column("contributing_event_ids", JSONB, nullable=True),
        sa.Column("time_min", sa.DateTime(timezone=True), nullable=True),
        sa.Column("time_max", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correlation_hash", sa.String(length=64), nullable=False),
        sa.Column("produced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_finding_tenant_unprocessed", "finding", ["tenant_id", "processed_at"])
    op.create_index("ix_finding_tenant_severity_time", "finding", ["tenant_id", "severity", "produced_at"])
    op.create_index("ix_finding_hash_time", "finding", ["correlation_hash", "produced_at"])
    op.create_index("ix_finding_source_type", "finding", ["source_type"])


def downgrade() -> None:
    op.drop_index("ix_finding_source_type", table_name="finding")
    op.drop_index("ix_finding_hash_time", table_name="finding")
    op.drop_index("ix_finding_tenant_severity_time", table_name="finding")
    op.drop_index("ix_finding_tenant_unprocessed", table_name="finding")
    op.drop_table("finding")
