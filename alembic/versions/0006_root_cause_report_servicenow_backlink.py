"""add ServiceNow back-link columns to root_cause_report

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-29

When the ServiceNowReportSink creates or updates a ticket, it writes back
the ticket number, sys_id, and a deep-link URL onto the report row. The UI
uses these to show a clickable jump-to-ServiceNow link.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("root_cause_report",
                  sa.Column("servicenow_number", sa.String(length=64), nullable=True))
    op.add_column("root_cause_report",
                  sa.Column("servicenow_sys_id", sa.String(length=64), nullable=True))
    op.add_column("root_cause_report",
                  sa.Column("servicenow_url",    sa.String(length=500), nullable=True))


def downgrade() -> None:
    op.drop_column("root_cause_report", "servicenow_url")
    op.drop_column("root_cause_report", "servicenow_sys_id")
    op.drop_column("root_cause_report", "servicenow_number")