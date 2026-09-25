"""partial unique indexes for natural-key lookup-or-create

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-25

Required by app.readers.persistence which performs lookup-or-create for
ProcessDefinition (by tenant + process_name) and ProcessCase (by
tenant + process + case_reference_id). Without these constraints, retried
or concurrent writes can produce duplicate rows.

Both indexes are partial — they only apply when the natural key is non-NULL,
so infra rows that have NULL case_reference_id can coexist freely.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_process_definition_tenant_name",
        "process_definition",
        ["tenant_id", "process_name"],
        unique=True,
    )
    op.create_index(
        "uq_process_case_tenant_process_ref",
        "process_case",
        ["tenant_id", "process_id", "case_reference_id"],
        unique=True,
        postgresql_where=op.f("case_reference_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_process_case_tenant_process_ref", table_name="process_case")
    op.drop_index("uq_process_definition_tenant_name", table_name="process_definition")
