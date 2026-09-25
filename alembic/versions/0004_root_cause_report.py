"""create root_cause_report table (tier-2 investigator output, UI input)

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "root_cause_report",
        sa.Column("report_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("investigator_class", sa.String(length=255), nullable=False),
        sa.Column("trigger_finding_id", sa.BigInteger(),
                  sa.ForeignKey("finding.finding_id", ondelete="SET NULL"), nullable=True),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("evidence_chain", JSONB, nullable=True),
        sa.Column("related_event_ids", JSONB, nullable=True),
        sa.Column("related_finding_ids", JSONB, nullable=True),
        sa.Column("correlation_keys", JSONB, nullable=True),
        sa.Column("payload", JSONB, nullable=True),
        sa.Column("produced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_root_cause_report_tenant_time", "root_cause_report", ["tenant_id", "produced_at"])
    op.create_index("ix_root_cause_report_tenant_severity", "root_cause_report", ["tenant_id", "severity"])
    op.create_index("ix_root_cause_report_trigger", "root_cause_report", ["trigger_finding_id"])


def downgrade() -> None:
    op.drop_index("ix_root_cause_report_trigger", table_name="root_cause_report")
    op.drop_index("ix_root_cause_report_tenant_severity", table_name="root_cause_report")
    op.drop_index("ix_root_cause_report_tenant_time", table_name="root_cause_report")
    op.drop_table("root_cause_report")
