"""APIFICATION: named-view registry — the escape hatch for UI read queries.

The Streamlit UI never sends SQL over the wire. Instead it names one of the
views registered here; the server binds params and runs the SQL. This keeps
the UI dumb, the SQL centralized, and rejects anything not on the whitelist.

A view is either:
  - a literal SQL string with ``:param`` placeholders, OR
  - a callable ``builder(params) -> (sql, effective_params)`` for views whose
    WHERE clause is conditional on which filters were supplied (see
    events.explorer).

Every view enforces its own ``tenant_id`` binding on the caller side by
requiring ``t`` in the params.
"""
from __future__ import annotations

from typing import Any, Callable, Union

# view -> either a plain SQL string, or a builder callable
_ViewValue = Union[str, Callable[[dict], tuple[str, dict]]]


def _events_explorer(params: dict) -> tuple[str, dict]:
    """Dynamic view: WHERE `server_id ILIKE :srv` is optional.

    Required params: t (tenant_id), src (list[str]), lim (int)
    Optional params: srv (str) — substring match on server_id
    """
    p = dict(params)
    sql = (
        "SELECT event_id, source_type, timestamp, server_id, system_id, actor_id, "
        "case_id, activity_name, lifecycle_stage, metadata_json "
        "FROM event_log "
        "WHERE tenant_id = :t AND source_type = ANY(:src) "
    )
    srv = (p.get("srv") or "").strip()
    if srv:
        sql += "AND server_id ILIKE :srv "
        p["srv"] = f"%{srv}%"
    else:
        p.pop("srv", None)
    sql += "ORDER BY timestamp DESC LIMIT :lim"
    return sql, p


def _live_recent_correlations(params: dict) -> tuple[str, dict]:
    """Dynamic view: optional ``now() - interval '<win>'`` clause.

    Required params: t
    Optional params: win (str) — interval literal like ``15 minutes``, ``24 hours``
    """
    p = {"t": params["t"]}
    win = params.get("win")
    if win:
        # Whitelist of allowed interval literals (never trust caller with SQL).
        allowed = {"15 minutes", "1 hour", "24 hours", "7 days", "30 days"}
        if win not in allowed:
            raise ValueError(f"unknown window: {win!r}")
        # win is a literal from the allow-list; safe to interpolate.
        sql = (
            "SELECT DISTINCT correlation_id FROM pipeline_event "
            f"WHERE tenant_id = :t AND created_at >= now() - interval '{win}' "
            "AND correlation_id IS NOT NULL"
        )
    else:
        sql = (
            "SELECT DISTINCT correlation_id FROM pipeline_event "
            "WHERE tenant_id = :t AND correlation_id IS NOT NULL"
        )
    return sql, p


VIEWS: dict[str, _ViewValue] = {
    # ── Overview page ─────────────────────────────────────────────────────
    "overview.event_count":
        "SELECT COUNT(*) FROM event_log WHERE tenant_id=:t",
    "overview.finding_count":
        "SELECT COUNT(*) FROM finding WHERE tenant_id=:t",
    "overview.report_count":
        "SELECT COUNT(*) FROM root_cause_report WHERE tenant_id=:t",
    "overview.case_count":
        "SELECT COUNT(*) FROM process_case WHERE tenant_id=:t",
    "overview.events_by_source": """
        SELECT source_type, COUNT(*) AS events,
               COUNT(DISTINCT server_id) AS hosts,
               COUNT(DISTINCT case_id)   AS cases
        FROM event_log WHERE tenant_id = :t
        GROUP BY source_type ORDER BY events DESC
    """,
    "overview.findings_by_source_severity": """
        SELECT source_type, severity, COUNT(*) AS n
        FROM finding WHERE tenant_id = :t
        GROUP BY source_type, severity ORDER BY source_type, severity DESC
    """,
    "overview.recent_reports": """
        SELECT report_id, produced_at, severity, summary, trigger_finding_id,
               servicenow_number, servicenow_url
        FROM root_cause_report WHERE tenant_id = :t
        ORDER BY produced_at DESC LIMIT 10
    """,

    # ── Root-Cause Reports page ──────────────────────────────────────────
    "reports.list_by_severity": """
        SELECT report_id, produced_at, severity, summary, trigger_finding_id,
               trigger_finding_ids, investigator_class, evidence_chain,
               correlation_keys, payload, related_event_ids, related_finding_ids,
               servicenow_number, servicenow_sys_id, servicenow_url
        FROM root_cause_report
        WHERE tenant_id = :t AND severity = ANY(:sev)
        ORDER BY produced_at DESC
    """,

    # ── Actions checklist (per report) ───────────────────────────────────
    "actions.for_report": """
        SELECT action_id, sequence_number, action_text, state,
               action_type, command, manual_steps,
               approver_id, decided_at, decision_note,
               executed_at, execution_output, exit_code,
               verified_at, verify_result
        FROM remediation_action
        WHERE report_id = :rid
        ORDER BY action_type DESC, sequence_number
    """,

    # ── Pending Approvals page ───────────────────────────────────────────
    "approvals.queue": """
        SELECT ra.action_id, ra.sequence_number, ra.action_text, ra.state,
               ra.approver_id, ra.decided_at, ra.decision_note,
               ra.report_id, rcr.severity, rcr.summary, rcr.payload,
               rcr.servicenow_number, rcr.servicenow_url
        FROM remediation_action ra
        JOIN root_cause_report  rcr ON rcr.report_id = ra.report_id
        WHERE ra.tenant_id = :t AND ra.state = ANY(:states)
        ORDER BY rcr.produced_at DESC, ra.sequence_number
    """,

    # ── Findings page ────────────────────────────────────────────────────
    "findings.source_options":
        "SELECT DISTINCT source_type FROM finding WHERE tenant_id = :t ORDER BY source_type",
    "findings.list_by_filter": """
        SELECT finding_id, source_type, severity, subject_key, observation,
               server_ids, case_ids, actor_ids,
               time_min, time_max, produced_at, processed_at, payload
        FROM finding
        WHERE tenant_id = :t
          AND source_type = ANY(:src)
          AND severity    = ANY(:sev)
        ORDER BY produced_at DESC
    """,

    # ── Events Explorer page ─────────────────────────────────────────────
    "events.source_options":
        "SELECT DISTINCT source_type FROM event_log WHERE tenant_id = :t ORDER BY source_type",
    "events.explorer": _events_explorer,

    # ── Cases page ───────────────────────────────────────────────────────
    "cases.list": """
        SELECT c.case_id, c.case_reference_id, c.status, c.start_time, c.end_time,
               pd.process_name, COUNT(e.event_id) AS event_count
        FROM process_case c
        LEFT JOIN process_definition pd ON pd.process_id = c.process_id
        LEFT JOIN event_log e ON e.case_id = c.case_id
        WHERE c.tenant_id = :t
        GROUP BY c.case_id, c.case_reference_id, c.status, c.start_time, c.end_time, pd.process_name
        ORDER BY c.case_id
    """,
    "cases.events_for_ref": """
        SELECT timestamp, source_type, activity_name, lifecycle_stage,
               actor_id, server_id, metadata_json
        FROM event_log
        WHERE tenant_id = :t
          AND case_id = (
              SELECT case_id FROM process_case
              WHERE tenant_id = :t AND case_reference_id = :ref
              LIMIT 1
          )
        ORDER BY timestamp
    """,

    # ── Live Activity page ───────────────────────────────────────────────
    "live.pending_exec_count":
        "SELECT count(*) AS n FROM remediation_action "
        "WHERE tenant_id = :t AND state = 'pending' AND action_type = 'executable'",
    "live.recent_correlations": _live_recent_correlations,
    "live.timeline_for_corrs":
        "SELECT event_id, created_at, correlation_id, run_id, stage, step, status, "
        "level, message, meta FROM pipeline_event "
        "WHERE tenant_id = :t AND correlation_id = ANY(:corrs) "
        "ORDER BY event_id DESC LIMIT 800",
    "live.awaiting_corrs":
        "SELECT DISTINCT r.payload->>'timeline_correlation_id' AS corr "
        "FROM remediation_action ra JOIN root_cause_report r ON r.report_id = ra.report_id "
        "WHERE ra.tenant_id = :t AND ra.state = 'pending' AND ra.action_type = 'executable'",
    "live.inprogress_corrs":
        "SELECT DISTINCT r.payload->>'timeline_correlation_id' AS corr "
        "FROM remediation_action ra JOIN root_cause_report r ON r.report_id = ra.report_id "
        "WHERE ra.tenant_id = :t AND ra.state IN ('approved','executing','verifying')",
    "live.rca_by_corr":
        "SELECT payload->>'timeline_correlation_id' AS corr, report_id, severity, "
        "summary, servicenow_number FROM root_cause_report "
        "WHERE tenant_id = :t AND payload->>'timeline_correlation_id' = ANY(:corrs) "
        "ORDER BY report_id DESC",

    # ── Approval telemetry lookup (used by /actions/{id}/decide server-side) ─
    "actions.correlation_for_action":
        "SELECT r.payload->>'timeline_correlation_id' AS corr, ra.tenant_id AS tenant "
        "FROM remediation_action ra JOIN root_cause_report r ON r.report_id = ra.report_id "
        "WHERE ra.action_id = :a",
}


def resolve(view_name: str, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return (sql, effective_params) for a whitelisted view."""
    if view_name not in VIEWS:
        raise KeyError(f"unknown view: {view_name!r}")
    v = VIEWS[view_name]
    if callable(v):
        return v(params)
    return v, dict(params)
