"""source_config (operator-managed key/value settings per source type)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-01

Written by core-service on behalf of the independent config-service. One row
per (tenant, source_type, key); saving the same key again overwrites the value.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "source_config",
        sa.Column("config_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "tenant_id", sa.Integer(),
            sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("config_key", sa.String(length=128), nullable=False),
        sa.Column("config_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("tenant_id", "source_type", "config_key", name="uq_source_config_tenant_source_key"),
    )


def downgrade() -> None:
    op.drop_table("source_config")
