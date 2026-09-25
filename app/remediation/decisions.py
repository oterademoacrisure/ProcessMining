"""Action-decision helpers.

The Streamlit UI calls `decide_action(...)` when an operator clicks one of:
  - Approve
  - Reject
  - Mark Done Manually
  - (or reverts to) Pending

It UPDATEs the `remediation_action` row with the new state, approver, note,
and timestamp, AND posts a comment on the associated ServiceNow ticket so
the SNOW activity log reflects the decision.

Phase B intentionally does NOT execute approved actions. That's Phase D
(LangGraph remediation pipeline). "Approved" here just records intent.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import RemediationAction, RootCauseReport as RootCauseReportRow
from app.db.session import SessionLocal
from app.integrations.servicenow_client import post_comment


log = logging.getLogger(__name__)


VALID_STATES = ("pending", "approved", "rejected", "done_manually", "skipped")

# States produced by the LangGraph remediation workflow. The UI must NOT
# manually transition out of these — the workflow owns them. decide_action
# rejects any attempt to mutate when the row is in one of these states.
WORKFLOW_OWNED_STATES = frozenset({
    "executing", "verifying",
})

# Terminal states from the workflow. The operator CAN reset these back to
# pending (e.g. to re-attempt remediation), but only via the explicit
# "reset" decision.
WORKFLOW_TERMINAL_STATES = frozenset({
    "failed", "verified", "unverified",
})

_VERB = {
    "approved":      "APPROVED",
    "rejected":      "REJECTED",
    "done_manually": "MARKED DONE MANUALLY",
    "skipped":       "SKIPPED",
    "pending":       "RE-OPENED (pending)",
}


def decide_action(
    action_id: int,
    new_state: str,
    approver_id: str | None = None,
    note: str | None = None,
) -> dict:
    """Persist the decision and (best-effort) post a ServiceNow comment.

    Returns a dict with `{"ok": bool, "snow_posted": bool, "message": str}`.
    """
    if new_state not in VALID_STATES:
        return {"ok": False, "snow_posted": False,
                "message": f"invalid state: {new_state!r}"}

    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        row = session.get(RemediationAction, action_id)
        if row is None:
            return {"ok": False, "snow_posted": False,
                    "message": f"action {action_id} not found"}

        # State-lock: don't let manual UI decisions override an in-flight
        # workflow. Once executing/verifying, the LangGraph workflow owns
        # the action until it reaches a terminal state.
        if row.state in WORKFLOW_OWNED_STATES:
            return {
                "ok": False,
                "snow_posted": False,
                "message": (
                    f"action {action_id} is {row.state!r} (workflow in progress); "
                    "wait for the workflow to finish before changing state"
                ),
            }

        # Terminal workflow states (failed/verified/unverified) CAN be reset
        # to pending — that's the operator's escape hatch to re-attempt.
        # Other transitions out of terminal states should go through pending
        # first so the audit trail is clean.
        if row.state in WORKFLOW_TERMINAL_STATES and new_state != "pending":
            return {
                "ok": False,
                "snow_posted": False,
                "message": (
                    f"action {action_id} is in terminal workflow state {row.state!r}; "
                    "reset to pending first if you want to re-decide"
                ),
            }

        row.state = new_state
        row.approver_id = (approver_id or "").strip() or None
        row.decision_note = (note or "").strip() or None
        row.decided_at = now if new_state != "pending" else None

        # Reset to pending = explicit "start over". Wipe any stale execution
        # outputs from a previous workflow attempt so the next run starts
        # with a clean slate (and the UI doesn't show last-run's REJECTED
        # text on a fresh EXECUTING state).
        if new_state == "pending":
            row.execution_output = None
            row.exit_code        = None
            row.executed_at      = None
            row.verify_result    = None
            row.verified_at      = None

        # Look up SNOW info via the parent report so we can post a comment.
        report = session.get(RootCauseReportRow, row.report_id)
        snow_sys_id = report.servicenow_sys_id if report else None

        session.commit()

        # Capture details for the SNOW comment AFTER commit.
        comment_text = _build_decision_comment(row, now, new_state)

    # Post to ServiceNow outside the session — best-effort.
    snow_posted = False
    if snow_sys_id and new_state != "pending":
        snow_posted = post_comment(snow_sys_id, comment_text)

    return {
        "ok":          True,
        "snow_posted": snow_posted,
        "message":     f"action {action_id} -> {new_state}"
                       + ("" if snow_posted or not snow_sys_id else " (SNOW comment failed)"),
    }


def _build_decision_comment(row: RemediationAction, when: datetime, state: str) -> str:
    verb = _VERB.get(state, state.upper())
    lines = [
        f"=== ACTION {verb} ===",
        f"Action #{row.sequence_number}: {row.action_text}",
        f"By:   {row.approver_id or '(anonymous)'}",
        f"When: {when.isoformat(timespec='seconds')}",
    ]
    if row.decision_note:
        lines.append(f"Note: {row.decision_note}")
    return "\n".join(lines)