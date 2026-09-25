"""POINT 21 (Task #21): pipeline_event (live stage telemetry)

Revision ID: 0014
Revises: 0013
Create Date: 2026-06-30

POINT 21: append-only feed of pipeline stage events that powers the real-time
"what's happening now" timeline in the UI. Pure telemetry — never read by the
pipeline. Indexed for (a) per-incident timelines and (b) the global recent feed.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_event",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id", sa.Integer(),
            sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=128), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=32), nullable=False),
        sa.Column("step", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="started"),
        sa.Column("level", sa.String(length=16), nullable=False, server_default="info"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_pipeline_event_tenant_corr", "pipeline_event",
        ["tenant_id", "correlation_id", "event_id"],
    )
    op.create_index(
        "ix_pipeline_event_tenant_created", "pipeline_event",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_event_tenant_created", table_name="pipeline_event")
    op.drop_index("ix_pipeline_event_tenant_corr", table_name="pipeline_event")
    op.drop_table("pipeline_event")
