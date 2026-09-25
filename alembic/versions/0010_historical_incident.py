"""POINT 19 (Task #19): historical_incident table — ServiceNow records as RCA precedent

Revision ID: 0010
Revises: 0009
Create Date: 2026-06-19

POINT 19: Adds the `historical_incident` table — a store of PAST (resolved /
closed) ServiceNow incidents and their resolution notes, kept separate from
the live pipeline tables (event_log / finding / root_cause_report). The RCA
investigator later retrieves similar past incidents from here as precedent
("we've seen this before — here's how it was fixed").

Retrieval (Phase B) uses:
  - structural match: correlation_id (exact recurrence), cmdb_ci / category
  - text match: the generated `search_tsv` full-text column (Postgres FTS).
pgvector is unavailable on this instance, so we use built-in full-text search;
the design stays embedding-ready (a future rerank stage can be added without
schema changes, or via a later additive migration for an embedding column).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # POINT 19: the historical-incident store. All columns mirror the fields we
    # pull from ServiceNow, plus raw_json (the full original payload, so future
    # features can use fields we didn't model without a re-sync).
    op.create_table(
        "historical_incident",
        sa.Column("incident_id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.Integer(),
                  sa.ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False),
        sa.Column("number", sa.String(length=64), nullable=True),
        sa.Column("sys_id", sa.String(length=64), nullable=True),
        sa.Column("short_description", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("close_notes", sa.Text(), nullable=True),    # POINT 19: the resolution — the precedent payload
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("subcategory", sa.String(length=128), nullable=True),
        sa.Column("cmdb_ci", sa.String(length=255), nullable=True),   # POINT 19: affected system, stored as a resolved NAME (not a sys_id)
        sa.Column("priority", sa.String(length=16), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sys_updated_on", sa.DateTime(timezone=True), nullable=True),  # POINT 19: source last-update time; MAX(this) per tenant = the incremental-sync watermark
        sa.Column("correlation_id", sa.String(length=100), nullable=True),  # POINT 19: recurrence-match key (matches our RCA sink's correlation_id)
        sa.Column("source_system", sa.String(length=32), nullable=False, server_default="servicenow"),
        sa.Column("raw_json", JSONB, nullable=True),           # POINT 19: full original ticket, future-proofing
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # POINT 19: DB-generated full-text column. Postgres auto-computes the search
    # tokens from the 3 text fields; we never write it. to_tsvector(regconfig,
    # text) is IMMUTABLE, so it's valid inside a STORED generated column. Done
    # in raw SQL because Alembic's generated-column support is awkward.
    op.execute(
        """
        ALTER TABLE historical_incident
        ADD COLUMN search_tsv tsvector
        GENERATED ALWAYS AS (
            to_tsvector('english',
                coalesce(short_description, '') || ' ' ||
                coalesce(description, '')       || ' ' ||
                coalesce(close_notes, ''))
        ) STORED
        """
    )

    # POINT 19: GIN index makes full-text matching fast across many tickets.
    op.create_index(
        "ix_historical_incident_search_tsv", "historical_incident",
        ["search_tsv"], postgresql_using="gin",
    )
    # POINT 19: structural-match + scoping indexes
    op.create_index("ix_historical_incident_tenant", "historical_incident", ["tenant_id"])
    op.create_index("ix_historical_incident_correlation", "historical_incident", ["correlation_id"])
    op.create_index("ix_historical_incident_cmdb_ci", "historical_incident", ["cmdb_ci"])
    # POINT 19: index for the watermark query — MAX(sys_updated_on) WHERE tenant_id=?
    op.create_index("ix_historical_incident_tenant_updated", "historical_incident", ["tenant_id", "sys_updated_on"])
    # POINT 19: dedup key — the DB itself enforces "no duplicate ticket", which
    # is what makes the loader's upsert safe and re-runnable.
    op.create_unique_constraint(
        "uq_historical_incident_tenant_sysid", "historical_incident", ["tenant_id", "sys_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_historical_incident_tenant_sysid", "historical_incident", type_="unique")
    op.drop_index("ix_historical_incident_tenant_updated", table_name="historical_incident")
    op.drop_index("ix_historical_incident_cmdb_ci", table_name="historical_incident")
    op.drop_index("ix_historical_incident_correlation", table_name="historical_incident")
    op.drop_index("ix_historical_incident_tenant", table_name="historical_incident")
    op.drop_index("ix_historical_incident_search_tsv", table_name="historical_incident")
    op.drop_table("historical_incident")
