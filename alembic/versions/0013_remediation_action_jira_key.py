"""POINT 20 (Task #20): remediation_action.jira_key

Revision ID: 0013
Revises: 0012
Create Date: 2026-06-29

POINT 20: stores the Jira ticket key raised (via MCP) for a command execution, so
we have traceability and can dedupe (never file two tickets for the same action).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remediation_action", sa.Column("jira_key", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("remediation_action", "jira_key")
