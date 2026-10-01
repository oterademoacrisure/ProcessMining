"""source_config: store each entry as one JSON document

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-01

Replaces the source_type / config_key / config_value columns with a single
JSONB column `config` = {"source_type": ..., "key": ..., "value": ...}.
Existing rows are carried over. Uniqueness (one value per tenant + source
type + key) moves to an expression index on the JSON fields.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("source_config", sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.execute(
        "UPDATE source_config SET config = jsonb_build_object("
        "'source_type', source_type, 'key', config_key, 'value', config_value)"
    )
    op.alter_column("source_config", "config", nullable=False)
    op.drop_constraint("uq_source_config_tenant_source_key", "source_config", type_="unique")
    op.drop_column("source_config", "config_value")
    op.drop_column("source_config", "config_key")
    op.drop_column("source_config", "source_type")
    op.execute(
        "CREATE UNIQUE INDEX uq_source_config_tenant_source_key ON source_config "
        "(tenant_id, (config->>'source_type'), (config->>'key'))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX uq_source_config_tenant_source_key")
    op.add_column("source_config", sa.Column("source_type", sa.String(length=32), nullable=True))
    op.add_column("source_config", sa.Column("config_key", sa.String(length=128), nullable=True))
    op.add_column("source_config", sa.Column("config_value", sa.Text(), nullable=True))
    op.execute(
        "UPDATE source_config SET source_type = config->>'source_type', "
        "config_key = config->>'key', config_value = config->>'value'"
    )
    for col in ("source_type", "config_key", "config_value"):
        op.alter_column("source_config", col, nullable=False)
    op.drop_column("source_config", "config")
    op.create_unique_constraint(
        "uq_source_config_tenant_source_key", "source_config",
        ["tenant_id", "source_type", "config_key"],
    )
