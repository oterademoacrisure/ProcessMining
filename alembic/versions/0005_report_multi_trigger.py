"""add trigger_finding_ids to root_cause_report (incident-level reports)

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-26

Tier-2 investigations now run per-INCIDENT (a group of correlated findings)
instead of per-finding. One incident can be triggered by multiple findings;
this column tracks all of them. The existing `trigger_finding_id` column
stays as the "primary" trigger (the highest-severity finding in the group)
for FK joins and existing UI compatibility.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "root_cause_report",
        sa.Column("trigger_finding_ids", JSONB, nullable=True),
    )
    # Backfill: existing rows had only one trigger.
    op.execute(
        """
        UPDATE root_cause_report
        SET trigger_finding_ids = jsonb_build_object(
              'values', jsonb_build_array(trigger_finding_id)
            )
        WHERE trigger_finding_id IS NOT NULL AND trigger_finding_ids IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("root_cause_report", "trigger_finding_ids")
