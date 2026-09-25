"""Persists LLM-recommended actions as rows in `remediation_action`.

Runs as a sink after RootCauseReportEmitterSink (which sets
`report.report_id` on the dataclass). Reads
`report.payload.llm_raw.recommended_actions` (a list of structured objects
in Option-B world, or a list of strings in legacy world) and inserts one
row per action.

Structured action object shape (LLM-produced):
  { "description": str, "type": "executable"|"advisory",
    "command": str|None, "manual_steps": str|None }

Legacy fallback: a bare string becomes an `advisory` row with no command.

Defensive filters:
  - investigatory verbs ("Investigate", "Analyze", ...) are dropped — the
    investigation IS the report
  - rows with empty descriptions are skipped
"""
from __future__ import annotations

import logging
from typing import Any

from app.db.models import RemediationAction
from app.db.session import SessionLocal
from app.findings import RootCauseReport
from app.sinks.report_sinks import BaseReportSink
from app.observability import progress  # POINT 21 (Task #21): live stage events


log = logging.getLogger(__name__)


# Defensive filter: drop "actions" whose description is investigatory.
# The LLM is told in the prompt not to emit these, but real LLMs slip
# sometimes — this is the safety net.
INVESTIGATORY_VERBS = frozenset({
    "investigate", "investigating", "investigation",
    "analyze",  "analyzing",  "analysis",
    "analyse",  "analysing",
    "review",   "reviewing",
    "identify", "identifying", "identification",
    "determine", "determining",
    "examine", "examining", "examination",
    "study",   "studying",
    "assess",  "assessing", "assessment",
    "evaluate","evaluating","evaluation",
    "look",    "check",
})


_VALID_TYPES = ("executable", "advisory")


def _is_investigatory(text: str) -> bool:
    first = text.strip().split(maxsplit=1)[0].lower().rstrip(",.:;!?")
    return first in INVESTIGATORY_VERBS


class RemediationActionEmitterSink(BaseReportSink):
    """Splits the LLM's recommended_actions into rows in remediation_action,
    classifying each as executable or advisory."""

    def handle(self, report: RootCauseReport) -> None:
        if not report.report_id:
            log.warning(
                "RemediationActionEmitterSink: report has no report_id (emitter "
                "sink must run before this one) — skipping action persistence"
            )
            return

        parsed = _extract_actions(report.payload)
        if not parsed:
            return

        tenant_id = (report.correlation_keys or {}).get("tenant_id")
        if not isinstance(tenant_id, int):
            log.warning(
                "RemediationActionEmitterSink: no tenant_id resolvable — skipping"
            )
            return

        rows = []
        for i, item in enumerate(parsed, start=1):
            rows.append(RemediationAction(
                tenant_id=tenant_id,
                report_id=report.report_id,
                sequence_number=i,
                action_text=item["description"],
                action_type=item["type"],
                command=item["command"],
                manual_steps=item["manual_steps"],
                state="pending",
            ))

        # Compute counts BEFORE commit — once the session closes, ORM
        # instances are detached and attribute access triggers a reload
        # that can't be satisfied without an active session.
        counts = {t: sum(1 for it in parsed if it["type"] == t) for t in _VALID_TYPES}

        with SessionLocal() as session:
            session.add_all(rows)
            session.commit()

        log.info(
            "remediation_action_emitter: persisted %d action(s) for report_id=%d  (executable=%d, advisory=%d)",
            len(rows), report.report_id, counts["executable"], counts["advisory"],
        )

        # POINT 21 (Task #21): an EXECUTABLE fix needs a human to approve before it runs.
        # Emit an "awaiting" event so the timeline (and the UI banner) tell the operator
        # to go approve — otherwise the thread goes quiet after the ticket with no cue.
        if counts["executable"]:
            corr = (report.payload or {}).get("timeline_correlation_id")
            progress.emit("APPROVAL", "started", tenant_id=tenant_id, correlation_id=corr,
                          step="awaiting human approval",
                          message=f"{counts['executable']} fix(es) need approval — open 'Pending Approvals'")


def _extract_actions(payload: Any) -> list[dict]:
    """Pull recommended_actions from the LLM output safely. Returns a list of
    dicts with keys (description, type, command, manual_steps).

    Handles both structured objects (Option B) and legacy strings (Option A
    backward compat). Drops investigatory non-actions and empty entries.
    """
    if not isinstance(payload, dict):
        return []
    llm_raw = payload.get("llm_raw")
    if not isinstance(llm_raw, dict):
        return []
    raw_actions = llm_raw.get("recommended_actions") or []
    if not isinstance(raw_actions, list):
        return []

    kept: list[dict] = []
    dropped: list[str] = []
    for a in raw_actions:
        parsed = _normalize_one(a)
        if parsed is None:
            continue
        if _is_investigatory(parsed["description"]):
            dropped.append(parsed["description"])
            continue
        kept.append(parsed)

    if dropped:
        log.warning(
            "RemediationActionEmitterSink: dropped %d investigatory non-action(s): %s",
            len(dropped),
            [d[:80] + ("…" if len(d) > 80 else "") for d in dropped],
        )

    return kept


def _normalize_one(item: Any) -> dict | None:
    """Coerce one LLM-emitted action into the structured shape we persist.

    Two input forms accepted:
      1. Structured object: {description, type, command, manual_steps}
      2. Bare string (legacy): the whole string is treated as an `advisory`
         description with no command.
    """
    if isinstance(item, str):
        s = item.strip()
        if not s:
            return None
        return {
            "description":  s,
            "type":         "advisory",
            "command":      None,
            "manual_steps": s,
        }
    if not isinstance(item, dict):
        return None

    description = (item.get("description") or "").strip()
    if not description:
        return None

    raw_type = (item.get("type") or "").strip().lower()
    if raw_type not in _VALID_TYPES:
        # Heuristic fallback: presence of command -> executable; else advisory.
        raw_type = "executable" if item.get("command") else "advisory"

    command = item.get("command")
    if isinstance(command, str):
        command = command.strip() or None
    else:
        command = None

    manual_steps = item.get("manual_steps")
    if isinstance(manual_steps, str):
        manual_steps = manual_steps.strip() or None
    else:
        manual_steps = None

    # Schema-level fixup: executable rows must have a command; if missing,
    # demote to advisory and use the description as the manual steps.
    if raw_type == "executable" and not command:
        raw_type = "advisory"
        if not manual_steps:
            manual_steps = description

    return {
        "description":  description,
        "type":         raw_type,
        "command":      command,
        "manual_steps": manual_steps,
    }