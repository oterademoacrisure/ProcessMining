"""POINT 19 (Task #19): historical_incident.outcome — approved/rejected/unknown

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-24

POINT 19: adds `outcome` to historical_incident — whether a past incident's fix
was approved & worked, was rejected/ineffective, or is unknown. Sourced from the
ServiceNow close_code (Solved* -> approved, Not Solved* -> rejected) or set by an
operator HITL decision. Precedent retrieval uses it to label/prefer proven fixes
and warn against ones that were tried and did not work.

Additive + reversible: new NOT NULL column with server_default 'unknown', so
existing rows backfill automatically.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "historical_incident",
        sa.Column("outcome", sa.String(length=16), nullable=False, server_default="unknown"),
    )


def downgrade() -> None:
    op.drop_column("historical_incident", "outcome")
