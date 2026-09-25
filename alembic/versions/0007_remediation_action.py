"""create remediation_action table (LLM-recommended steps with per-action approval state)

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-29

One row per LLM-recommended action per report. Operators approve, reject,
or mark each as done-manually via the Streamlit UI. State transitions are
captured with approver_id, decided_at, and decision_note.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "remediation_action",
        sa.Column("action_id",       sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id",       sa.Integer(),
                  sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("report_id",       sa.BigInteger(),
                  sa.ForeignKey("root_cause_report.report_id", ondelete="CASCADE"), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("action_text",     sa.Text(),    nullable=False),
        sa.Column("state",           sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("approver_id",     sa.String(length=255), nullable=True),
        sa.Column("decided_at",      sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note",   sa.Text(),    nullable=True),
        sa.Column("created_at",      sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_remediation_action_report",       "remediation_action", ["report_id"])
    op.create_index("ix_remediation_action_tenant_state", "remediation_action", ["tenant_id", "state"])


def downgrade() -> None:
    op.drop_index("ix_remediation_action_tenant_state", table_name="remediation_action")
    op.drop_index("ix_remediation_action_report",       table_name="remediation_action")
    op.drop_table("remediation_action")