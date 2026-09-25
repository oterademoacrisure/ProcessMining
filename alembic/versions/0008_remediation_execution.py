"""add execution columns to remediation_action (Phase D tier-3 remediation)

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-02

When an action transitions through executing -> succeeded/failed -> verifying ->
verified/unverified, the LangGraph workflow stamps the outcome here:
  - executed_at      : when the executor ran
  - execution_output : stdout (and stderr suffix on failure)
  - exit_code        : 0 = success, non-zero = failure
  - verified_at      : when the verifier finished
  - verify_result    : human-readable outcome from the verifier
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remediation_action",
                  sa.Column("executed_at",      sa.DateTime(timezone=True), nullable=True))
    op.add_column("remediation_action",
                  sa.Column("execution_output", sa.Text(),                  nullable=True))
    op.add_column("remediation_action",
                  sa.Column("exit_code",        sa.Integer(),               nullable=True))
    op.add_column("remediation_action",
                  sa.Column("verified_at",      sa.DateTime(timezone=True), nullable=True))
    op.add_column("remediation_action",
                  sa.Column("verify_result",    sa.Text(),                  nullable=True))


def downgrade() -> None:
    for col in ("verify_result", "verified_at", "exit_code", "execution_output", "executed_at"):
        op.drop_column("remediation_action", col)