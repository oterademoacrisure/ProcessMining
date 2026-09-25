"""LangGraph workflow for Tier-3 remediation.

Graph shape:
   START -> VALIDATE -> (allow_listed?) -> EXECUTE -> (exit_code 0?) -> VERIFY -> (passed?) -> REPORT -> END
                              | no                       | no                       | no
                              +-> REPORT_REJECTED -> END +-> REPORT_FAILED -> END   +-> REPORT_UNVERIFIED -> END

Each node:
  - Reads the current RemediationState
  - Does its work (allow-list check / execute / verify / report)
  - Updates the corresponding columns on the `remediation_action` row
  - Posts a ServiceNow comment narrating the transition (via the helper in
    app/integrations/servicenow_client.py)

State is persisted in the DB at each node, so a runner crash mid-workflow
doesn't lose progress — the next remediation_dispatcher poll will resume
based on the action's current state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, TypedDict

from langgraph.graph import StateGraph, END
from sqlalchemy import select

from app.db.models import RemediationAction, RootCauseReport as RootCauseReportRow
from app.db.session import SessionLocal
from app.integrations.servicenow_client import post_comment, resolve_incident
from app.remediation.allow_list import evaluate, evaluate_command
from app.remediation.executor import BaseExecutor, build_executor
from app.remediation.verifier import BaseVerifier, build_verifier
from app.observability import progress  # POINT 21 (Task #21): live stage events


log = logging.getLogger(__name__)


def _tl(state) -> tuple:
    """POINT 21: (timeline correlation_id, tenant_id, affected_system) for emitting
    stage events onto the SAME incident thread the diagnosis stage started."""
    return (state.get("timeline_correlation_id"), state.get("tenant_id"),
            state.get("affected_system"))


class RemediationState(TypedDict, total=False):
    # Identifiers
    action_id:   int
    report_id:   int
    tenant_id:   int

    # Inputs
    action_text:        str
    persisted_command:  str | None   # from remediation_action.command (Option B)
    report_confidence:  float | None
    snow_sys_id:        str | None
    resolve_close_code: str          # close_code used when resolving the ticket on verify
    # POINT 21 (Task #21): MUST be declared here or LangGraph drops them from the
    # state between nodes (which orphaned the remediation timeline events).
    timeline_correlation_id: str | None
    affected_system:         str | None
    snow_number:             str | None

    # VALIDATE outputs
    allow_listed: bool
    command:      str | None
    deny_reason:  str

    # EXECUTE outputs
    exit_code:    int
    stdout:       str
    stderr:       str
    duration_sec: float

    # VERIFY outputs
    verify_passed:  bool
    verify_details: str

    # Final outcome
    final_state: str  # verified | unverified | failed | rejected


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _update_action(action_id: int, **fields) -> None:
    """Update an action row and commit. Keeps the workflow durable: each
    state transition lands in the DB before the next node runs."""
    with SessionLocal() as session:
        row = session.get(RemediationAction, action_id)
        if row is None:
            log.error("remediation workflow: action_id=%d not found", action_id)
            return
        for k, v in fields.items():
            setattr(row, k, v)
        session.commit()


def _snow_comment(snow_sys_id: str | None, text: str) -> None:
    if not snow_sys_id:
        return
    try:
        post_comment(snow_sys_id, text)
    except Exception:
        log.exception("remediation workflow: SNOW comment failed for sys_id=%s", snow_sys_id)


def _snow_resolve(snow_sys_id: str | None, close_notes: str, close_code: str) -> None:
    """POINT 19 (Step 7): resolve the ticket once remediation is verified — closes
    the ITSM loop (and lets a real-mode history sync pull it back as precedent).
    `close_code` must be a value valid on the target instance (see
    scripts/list_servicenow_close_codes.py)."""
    if not snow_sys_id:
        return
    try:
        resolve_incident(snow_sys_id, close_notes=close_notes, close_code=close_code)
    except Exception:
        log.exception("remediation workflow: SNOW resolve failed for sys_id=%s", snow_sys_id)


# ─────────────────────────────────────────────────────────────────────────────
# Nodes
# ─────────────────────────────────────────────────────────────────────────────

def _node_validate(state: RemediationState, executor: BaseExecutor) -> RemediationState:
    action_text = state["action_text"]
    snow        = state.get("snow_sys_id")

    # Don't change DB state here — validation may reject. Leaving the row at
    # 'approved' until either execute (success path) or report_rejected (deny
    # path) advances it avoids a misleading "EXECUTING" flash on the UI for
    # actions that never run.
    _snow_comment(snow, _build_snow_comment("VALIDATING", state, extra=f"action: {action_text[:200]}"))

    # Option B: if the LLM classified this action as executable, the command
    # is already persisted on the row. Use that as the primary source of
    # truth. Fall back to scraping from action_text for legacy rows.
    persisted_command = state.get("persisted_command")
    if persisted_command:
        result = evaluate_command(persisted_command)
    else:
        result = evaluate(action_text)

    state["allow_listed"] = result.allowed
    state["command"]      = result.command
    state["deny_reason"]  = result.reason
    # POINT 21 (Task #21): timeline — VALIDATE outcome.
    _corr, _tenant, _sys = _tl(state)
    progress.emit("VALIDATE", "succeeded" if result.allowed else "failed",
                  tenant_id=_tenant, correlation_id=_corr, step="Safety-checking the proposed fix",
                  level=("info" if result.allowed else "warn"),
                  message=(None if result.allowed else result.reason),
                  meta={"allowed": result.allowed, "command": result.command, "system": _sys})
    return state


def _node_execute(state: RemediationState, executor: BaseExecutor) -> RemediationState:
    action_id = state["action_id"]
    snow      = state.get("snow_sys_id")
    command   = state.get("command") or ""

    # Now we actually transition to executing — only after validate passed.
    _update_action(action_id, state="executing")
    _snow_comment(snow, _build_snow_comment("EXECUTING", state, extra=f"command: {command}"))

    _corr, _tenant, _sys = _tl(state)
    progress.emit("EXECUTE", "started", tenant_id=_tenant, correlation_id=_corr,
                  step="Applying the recommended fix", message=command, meta={"system": _sys})

    result = executor.execute(command)
    state["exit_code"]    = result.exit_code
    state["stdout"]       = result.stdout
    state["stderr"]       = result.stderr
    state["duration_sec"] = result.duration_sec

    output = ((result.stdout or "") + ("\n--- STDERR ---\n" + result.stderr if result.stderr else ""))[:8000]
    _update_action(
        action_id,
        executed_at=_now(),
        execution_output=output,
        exit_code=result.exit_code,
    )
    progress.emit("EXECUTE", "succeeded" if result.exit_code == 0 else "failed",
                  tenant_id=_tenant, correlation_id=_corr, step="Fix applied",
                  level=("info" if result.exit_code == 0 else "error"),
                  meta={"exit_code": result.exit_code, "duration_sec": result.duration_sec, "system": _sys})
    return state


def _node_verify(state: RemediationState, verifier: BaseVerifier) -> RemediationState:
    action_id = state["action_id"]
    snow      = state.get("snow_sys_id")

    _update_action(action_id, state="verifying")
    _snow_comment(snow, _build_snow_comment("VERIFYING", state,
                  extra=f"exit_code={state.get('exit_code')}; awaiting verifier"))

    _corr, _tenant, _sys = _tl(state)
    progress.emit("VERIFY", "started", tenant_id=_tenant, correlation_id=_corr,
                  step="Confirming the incident is resolved")

    vr = verifier.verify(
        action_text=state["action_text"],
        command=state.get("command") or "",
        report_confidence=state.get("report_confidence"),
    )
    state["verify_passed"]  = vr.passed
    state["verify_details"] = vr.details

    _update_action(
        action_id,
        verified_at=_now(),
        verify_result=vr.details,
    )
    progress.emit("VERIFY", "succeeded" if vr.passed else "failed",
                  tenant_id=_tenant, correlation_id=_corr, step="Confirmed the fix resolved the issue",
                  level=("info" if vr.passed else "warn"),
                  meta={"passed": vr.passed, "system": _sys})
    return state


def _node_report_rejected(state: RemediationState, **_) -> RemediationState:
    action_id = state["action_id"]
    snow      = state.get("snow_sys_id")
    reason    = state.get("deny_reason", "(no reason)")
    # NOTE: we deliberately do NOT set executed_at — the action did not run.
    # `execution_output` carries the reason so the UI can surface it, but
    # the timestamp column stays NULL so "Executed at" only ever means "the
    # command was actually attempted".
    _update_action(
        action_id,
        state="rejected",
        execution_output=f"[AUTO-REJECTED] {reason}",
    )
    _snow_comment(snow, _build_snow_comment("AUTO-REJECTED", state,
                  extra=f"reason: {reason}", final=True))
    state["final_state"] = "rejected"
    return state


def _node_report_failed(state: RemediationState, **_) -> RemediationState:
    action_id = state["action_id"]
    snow      = state.get("snow_sys_id")
    _update_action(action_id, state="failed")
    _snow_comment(snow, _build_snow_comment(
        "EXECUTION FAILED", state,
        extra=f"exit_code={state.get('exit_code')}\nstderr:\n{state.get('stderr', '')[:600]}",
        final=True,
    ))
    state["final_state"] = "failed"
    return state


def _node_report_verified(state: RemediationState, **_) -> RemediationState:
    action_id = state["action_id"]
    snow      = state.get("snow_sys_id")
    _update_action(action_id, state="verified")
    _snow_comment(snow, _build_snow_comment(
        "REMEDIATION VERIFIED", state,
        extra=state.get("verify_details", ""),
        final=True,
    ))
    # POINT 20: ServiceNow is RESOLVED in run_workflow AFTER the Jira ticket is filed,
    # so the resolution close-notes can cross-reference the Jira key. See run_workflow.
    state["final_state"] = "verified"
    return state


def _node_report_unverified(state: RemediationState, **_) -> RemediationState:
    action_id = state["action_id"]
    snow      = state.get("snow_sys_id")
    _update_action(action_id, state="unverified")
    _snow_comment(snow, _build_snow_comment(
        "REMEDIATION UNVERIFIED", state,
        extra=state.get("verify_details", "") + "\nManual review recommended.",
        final=True,
    ))
    state["final_state"] = "unverified"
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Graph wiring
# ─────────────────────────────────────────────────────────────────────────────

def build_graph(executor: BaseExecutor, verifier: BaseVerifier):
    g = StateGraph(RemediationState)

    g.add_node("validate",         lambda s: _node_validate(s, executor))
    g.add_node("execute",          lambda s: _node_execute(s, executor))
    g.add_node("verify",           lambda s: _node_verify(s, verifier))
    g.add_node("report_rejected",  _node_report_rejected)
    g.add_node("report_failed",    _node_report_failed)
    g.add_node("report_verified",  _node_report_verified)
    g.add_node("report_unverified",_node_report_unverified)

    g.set_entry_point("validate")

    g.add_conditional_edges(
        "validate",
        lambda s: "execute" if s.get("allow_listed") else "report_rejected",
        {"execute": "execute", "report_rejected": "report_rejected"},
    )
    g.add_conditional_edges(
        "execute",
        lambda s: "verify" if s.get("exit_code", -1) == 0 else "report_failed",
        {"verify": "verify", "report_failed": "report_failed"},
    )
    g.add_conditional_edges(
        "verify",
        lambda s: "report_verified" if s.get("verify_passed") else "report_unverified",
        {"report_verified": "report_verified", "report_unverified": "report_unverified"},
    )

    for terminal in ("report_rejected", "report_failed", "report_verified", "report_unverified"):
        g.add_edge(terminal, END)

    return g.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point used by the dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def run_workflow(action_id: int, config: dict | None = None) -> str:
    """Load the action + report + SNOW info from DB, then drive the graph.
    Returns the final state name."""
    cfg = config or {}

    # Pull action + report info
    with SessionLocal() as session:
        action = session.get(RemediationAction, action_id)
        if action is None:
            log.error("run_workflow: action_id=%d not found", action_id)
            return "missing"
        report = session.get(RootCauseReportRow, action.report_id)

        snow_sys_id = report.servicenow_sys_id if report else None
        snow_number = report.servicenow_number if report else None   # POINT 21: for the closing line
        # POINT 21 (Task #21): the timeline key the investigator saved — so remediation
        # events land on the SAME incident timeline as diagnosis/ticket.
        timeline_corr = (
            report.payload.get("timeline_correlation_id")
            if (report and isinstance(report.payload, dict)) else None
        )
        # POINT 21 (Task #21): the affected system/host, for tagging remediation events.
        affected_system = (
            (report.payload.get("primary_trigger") or {}).get("subject")
            if (report and isinstance(report.payload, dict)) else None
        )
        # Confidence may live in the report payload
        report_confidence = None
        if report and isinstance(report.payload, dict):
            llm_raw = report.payload.get("llm_raw")
            if isinstance(llm_raw, dict):
                raw_conf = llm_raw.get("confidence")
                try:
                    report_confidence = float(raw_conf) if raw_conf is not None else None
                except (TypeError, ValueError):
                    report_confidence = None

        initial_state: RemediationState = {
            "action_id":         action.action_id,
            "report_id":         action.report_id,
            "tenant_id":         action.tenant_id,
            "action_text":       action.action_text,
            "persisted_command": action.command,
            "report_confidence": report_confidence,
            "snow_sys_id":       snow_sys_id,
            "timeline_correlation_id": timeline_corr,   # POINT 21 (Task #21)
            "affected_system":   affected_system,       # POINT 21 (Task #21)
            "snow_number":       snow_number,           # POINT 21 (Task #21)
            # POINT 19: close_code used when resolving the ticket on verify — must be
            # a value valid on the target instance (see list_servicenow_close_codes.py).
            "resolve_close_code": cfg.get("resolve_close_code", "Solved (Permanently)"),
        }

    executor = build_executor(cfg)
    verifier = build_verifier(cfg)
    graph    = build_graph(executor, verifier)
    final    = graph.invoke(initial_state)
    final_state = final.get("final_state", "unknown")
    _corr, _tenant = final.get("timeline_correlation_id"), final.get("tenant_id")
    _sys = final.get("affected_system")

    # POINT 20 (Task #20): FIRST raise the Jira audit ticket for this command execution
    # (via MCP) and, on a verified fix, mark it Done. Done first so the ServiceNow
    # resolution below can cross-reference the Jira key. Fail-safe: never blocks remediation.
    jira_key = None
    if final_state != "rejected":
        progress.emit("JIRA_CREATE", "started", tenant_id=_tenant, correlation_id=_corr,
                      step="Logging an audit record in Jira")
    try:
        from app.remediation.jira_feedback import create_jira_ticket
        jira_key = create_jira_ticket(action_id, final_state, cfg)
    except Exception:
        log.exception("run_workflow: jira ticket failed (non-fatal)")
    if jira_key:
        progress.emit("JIRA_CREATE", "succeeded", tenant_id=_tenant, correlation_id=_corr,
                      step="Audit record logged in Jira",
                      meta={"jira_key": jira_key, "system": _sys,
                            "status": "Done" if final_state == "verified" else "Open"})

    # POINT 19/20 (Step 7): on a verified fix, RESOLVE the ServiceNow ticket — citing the
    # Jira key in the close-notes so the two records are cross-linked. Done after Jira.
    if final_state == "verified":
        progress.emit("SNOW_RESOLVE", "started", tenant_id=_tenant, correlation_id=_corr,
                      step="Closing the incident in ServiceNow")
        close_notes = (
            f"Automated remediation verified. Command: {final.get('command') or '(n/a)'}. "
            f"{final.get('verify_details', '')}"
        ).strip()
        if jira_key:
            close_notes += f" Jira: {jira_key}."
        _snow_resolve(
            final.get("snow_sys_id"),
            close_notes=close_notes,
            close_code=cfg.get("resolve_close_code") or "Solved (Permanently)",
        )
        _inc = final.get("snow_number")
        progress.emit("SNOW_RESOLVE", "succeeded", tenant_id=_tenant, correlation_id=_corr,
                      step="ServiceNow ticket closed",
                      message=f"ServiceNow {_inc or '(ticket)'} resolved — fixed via Jira {jira_key or '(n/a)'}",
                      meta={"inc": _inc, "jira_key": jira_key, "system": _sys})

    # POINT 19 (Step 7): feed this resolved incident back as precedent — verified
    # -> approved, failed/unverified -> rejected. Fail-safe: never blocks remediation.
    progress.emit("LEARN", "started", tenant_id=_tenant, correlation_id=_corr,
                  step="Saving this resolution for future incidents")
    try:
        from app.servicenow_history.feedback import capture_precedent
        capture_precedent(action_id, final_state, cfg)
        progress.emit("LEARN", "succeeded", tenant_id=_tenant, correlation_id=_corr,
                      step="Saved to institutional memory — the system just got smarter",
                      meta={"outcome": "approved" if final_state == "verified" else "rejected",
                            "system": _sys})
    except Exception:
        log.exception("run_workflow: precedent capture failed (non-fatal)")

    return final_state


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_snow_comment(stage: str, state: RemediationState, extra: str = "", final: bool = False) -> str:
    """Format a SNOW journal entry for one workflow transition."""
    lines = [
        f"=== REMEDIATION {stage} ===",
        f"action_id: {state.get('action_id')}",
        f"report_id: {state.get('report_id')}",
    ]
    if extra:
        lines.append("")
        lines.append(extra)
    if final:
        lines.append("")
        lines.append("(terminal state)")
    return "\n".join(lines)