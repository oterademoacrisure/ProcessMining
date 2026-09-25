"""initial multi-tenant schema (creates new tables, seeds default tenant)

Revision ID: 0001
Revises:
Create Date: 2026-05-17

Legacy tables (db_metrics, pending_approvals, incident_reports) are left in place.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenant",
        sa.Column("tenant_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_name", sa.String(length=255), nullable=False, unique=True),
        sa.Column("industry", sa.String(length=128), nullable=True),
        sa.Column("region", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
    )

    op.create_table(
        "process_definition",
        sa.Column("process_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("process_name", sa.String(length=255), nullable=False),
        sa.Column("process_version", sa.String(length=64), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_process_definition_tenant_id", "process_definition", ["tenant_id"])

    op.create_table(
        "process_case",
        sa.Column("case_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("process_id", sa.Integer(), sa.ForeignKey("process_definition.process_id", ondelete="CASCADE"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_reference_id", sa.String(length=255), nullable=True),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("case_owner", sa.String(length=255), nullable=True),
        sa.Column("case_metadata_json", JSONB, nullable=True),
    )
    op.create_index("ix_process_case_tenant_id", "process_case", ["tenant_id"])
    op.create_index("ix_process_case_process_id", "process_case", ["process_id"])

    op.create_table(
        "event_log",
        sa.Column("event_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("case_id", sa.BigInteger(), sa.ForeignKey("process_case.case_id", ondelete="SET NULL"), nullable=True),
        sa.Column("process_id", sa.Integer(), sa.ForeignKey("process_definition.process_id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("activity_name", sa.String(length=255), nullable=True),
        sa.Column("activity_type", sa.String(length=32), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lifecycle_stage", sa.String(length=32), nullable=True),
        sa.Column("actor_id", sa.String(length=255), nullable=True),
        sa.Column("system_id", sa.String(length=255), nullable=True),
        sa.Column("server_id", sa.String(length=255), nullable=True),
        sa.Column("sequence_number", sa.Integer(), nullable=True),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_event_log_tenant_unprocessed", "event_log", ["tenant_id", "processed_at"])
    op.create_index("ix_event_log_tenant_source", "event_log", ["tenant_id", "source_type"])
    op.create_index("ix_event_log_timestamp", "event_log", ["timestamp"])
    op.create_index("ix_event_log_case", "event_log", ["case_id"])

    op.execute(
        "INSERT INTO tenant (tenant_name, industry, region, status) "
        "VALUES ('default', 'internal', 'global', 'active')"
    )


def downgrade() -> None:
    op.drop_index("ix_event_log_case", table_name="event_log")
    op.drop_index("ix_event_log_timestamp", table_name="event_log")
    op.drop_index("ix_event_log_tenant_source", table_name="event_log")
    op.drop_index("ix_event_log_tenant_unprocessed", table_name="event_log")
    op.drop_table("event_log")

    op.drop_index("ix_process_case_process_id", table_name="process_case")
    op.drop_index("ix_process_case_tenant_id", table_name="process_case")
    op.drop_table("process_case")

    op.drop_index("ix_process_definition_tenant_id", table_name="process_definition")
    op.drop_table("process_definition")

    op.drop_table("tenant")
