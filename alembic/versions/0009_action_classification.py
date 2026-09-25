"""classify remediation actions as executable vs advisory

Revision ID: 0009
Revises: 0008
Create Date: 2026-06-02

The LLM now emits each recommended action as a structured object with:
  - action_type: "executable" (system can run it) or "advisory" (manual step
                 the operator does themselves; never goes through the
                 remediation workflow)
  - command:     the executable shell command (NULL for advisory)
  - manual_steps: the human-readable instructions (NULL for executable)

The UI splits actions into two visual sections so operators see at a
glance which steps the system can automate and which they must do by hand.
Phase B's "approve/reject/done/skip" controls still apply; the dispatcher
only picks up executable actions for the LangGraph workflow.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("remediation_action",
                  sa.Column("action_type",  sa.String(length=16), nullable=False,
                            server_default="advisory"))
    op.add_column("remediation_action",
                  sa.Column("command",      sa.Text(), nullable=True))
    op.add_column("remediation_action",
                  sa.Column("manual_steps", sa.Text(), nullable=True))

    # Existing rows from Phase B+D have no classification yet; default them
    # to advisory (safer — they won't auto-execute on the next dispatch).
    # The server_default above handles new rows; this line is a no-op for
    # already-inserted rows because the column is non-nullable.

    op.create_index(
        "ix_remediation_action_tenant_type_state",
        "remediation_action",
        ["tenant_id", "action_type", "state"],
    )


def downgrade() -> None:
    op.drop_index("ix_remediation_action_tenant_type_state", table_name="remediation_action")
    op.drop_column("remediation_action", "manual_steps")
    op.drop_column("remediation_action", "command")
    op.drop_column("remediation_action", "action_type")