"""Polls for approved remediation actions and hands each to the LangGraph
workflow (validate -> execute -> verify -> report).

Runs as a separate cycle in the runner, after the investigator dispatcher.
Tenant-scoped, batch-limited, idempotent (the workflow flips state away
from 'approved' as its first action, so subsequent polls won't re-pick it).
"""
from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RemediationAction
from app.remediation.langgraph_workflow import run_workflow


log = logging.getLogger(__name__)


class RemediationDispatcher:
    """Poll-and-run loop for approved actions."""

    def __init__(
        self,
        config: dict | None,
        tenant_ids: Sequence[int],
        batch_size: int = 20,
    ):
        self.config = config or {}
        self.tenant_ids = list(tenant_ids)
        self.batch_size = batch_size

    def run_once(self, session: Session) -> int:
        if not self.tenant_ids:
            return 0

        # Only pick up EXECUTABLE actions. Advisory items go through the
        # same approve/reject UI but stay tracking-only — operators perform
        # them outside the system.
        stmt = (
            select(RemediationAction)
            .where(RemediationAction.tenant_id.in_(self.tenant_ids))
            .where(RemediationAction.state == "approved")
            .where(RemediationAction.action_type == "executable")
            .order_by(RemediationAction.decided_at.asc())
            .limit(self.batch_size)
        )
        actions = list(session.scalars(stmt).all())
        if not actions:
            return 0

        log.info(
            "remediation_dispatcher: picked up %d approved action(s)",
            len(actions),
        )

        # Note: we deliberately invoke the workflow in this loop synchronously.
        # The workflow itself updates the DB at each node, so even if the
        # runner crashes mid-batch, partial state is preserved.
        for action in actions:
            try:
                final = run_workflow(action.action_id, config=self.config)
                log.info(
                    "remediation_dispatcher: action_id=%d -> final_state=%s",
                    action.action_id, final,
                )
            except Exception:
                log.exception(
                    "remediation_dispatcher: workflow crashed for action_id=%d",
                    action.action_id,
                )
        return len(actions)