"""Streamlit dashboard for the multi-tenant process-mining framework.

Reads the new schema (event_log, finding, root_cause_report, process_case,
process_definition). No write paths — pure viewer.

Run:
    streamlit run app/ui/streamlit_app.py
"""
import os
import sys
from contextlib import nullcontext

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

# APIFICATION: UI no longer opens a DB session. All reads go through the
# core-service /api/v1/query named-view endpoint, and the sole write path
# (action decisions) POSTs to /api/v1/actions/{id}/decide.
from services.ui import api_client  # noqa: E402

# APIFICATION: kept in sync with app/remediation/decisions.py :: VALID_STATES.
# Duplicated on the UI side to avoid importing app.remediation.* into the UI
# container (which has neither SQLAlchemy nor psycopg2 installed).
VALID_STATES = ("pending", "approved", "rejected", "done_manually", "skipped")


ACTION_STATE_COLOR = {
    "pending":       "#1890ff",
    "approved":      "#1890ff",   # waiting for workflow pickup — same blue, distinct from active
    "executing":     "#13c2c2",   # cyan — workflow running
    "verifying":     "#13c2c2",
    "verified":      "#52c41a",   # green — verified success
    "succeeded":     "#52c41a",   # alias
    "unverified":    "#fa8c16",   # orange — execution ran but didn't verify
    "failed":        "#ff4d4f",   # red — execution failed
    "rejected":      "#ff4d4f",
    "done_manually": "#722ed1",
    "skipped":       "#8c8c8c",
}

# States the LangGraph workflow owns — UI must not show transition buttons.
WORKFLOW_OWNED_STATES_UI = frozenset({"approved", "executing", "verifying"})
# Terminal states (from workflow OR operator decision). Any of these forces
# the operator to explicitly Reset before changing their mind — no
# accidental "rejected → approved" with one click.
TERMINAL_STATES_UI = frozenset({
    # workflow-produced terminals
    "failed", "verified", "unverified",
    # operator-produced terminals
    "rejected", "done_manually", "skipped",
})


def _action_badge(state: str) -> str:
    color = ACTION_STATE_COLOR.get(state, "#888")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:0.8em;font-weight:600;">{state.upper()}</span>'


def _safe_str(value) -> str | None:
    """Normalize a pandas cell value to a non-empty string, or None.

    Pandas reads NULL columns as either None (Object dtype) or NaN (float-
    coerced). Both should render as "missing" to the UI, but bool(NaN) is
    True, so a naïve `if cell:` check thinks there's content. This helper
    handles both.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    if not s or s.lower() == "nan":
        return None
    return s


def _confidence_badge(confidence: float | None) -> str:
    """Color-coded badge for LLM confidence. Returns empty string if None."""
    if confidence is None:
        return ""
    if confidence >= 0.7:
        color, label = "#52c41a", "HIGH"
    elif confidence >= 0.5:
        color, label = "#fa8c16", "MED"
    else:
        color, label = "#ff4d4f", "LOW"
    return (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8em;font-weight:600;">'
        f'CONF {label} {confidence:.2f}</span>'
    )


def _outcome_badge(outcome: str | None) -> str:
    """POINT 19: badge for a precedent's fix outcome — proven / failed / unknown."""
    o = (outcome or "unknown").lower()
    if o == "approved":
        color, label = "#52c41a", "✓ PROVEN FIX"
    elif o == "rejected":
        color, label = "#cf1322", "✗ TRIED — DIDN'T WORK"
    else:
        color, label = "#8c8c8c", "OUTCOME UNKNOWN"
    return (
        f'<span style="background:{color};color:white;padding:2px 8px;'
        f'border-radius:4px;font-size:0.8em;font-weight:600;">{label}</span>'
    )


def _extract_confidence(payload) -> float | None:
    if not isinstance(payload, dict):
        return None
    llm_raw = payload.get("llm_raw")
    if not isinstance(llm_raw, dict):
        return None
    raw = llm_raw.get("confidence")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _fetch_actions_for_report(report_id: int) -> pd.DataFrame:
    # APIFICATION: named view "actions.for_report".
    return _query_df("actions.for_report", rid=report_id)


def _handle_decision_click(action_id: int, new_state: str, note: str):
    """Wrapper around the backend helper that surfaces success/failure
    to the operator via st.toast (Streamlit ≥ 1.27)."""
    # APIFICATION: HTTP POST to core-service. The server-side handler also
    # emits the APPROVAL telemetry event when new_state == 'approved'.
    result = api_client.decide_action(
        action_id=action_id,
        new_state=new_state,
        approver_id=APPROVER_ID,
        note=note,
    )
    if result["ok"]:
        # Bust the read cache so the new state shows immediately on the next rerun.
        st.cache_data.clear()
        snow = "SNOW comment posted" if result["snow_posted"] else "SNOW comment skipped"
        st.toast(f"Action #{action_id} → {new_state} ({snow})", icon="✅")
    else:
        st.toast(f"Decision failed: {result['message']}", icon="⚠️")


def _render_action_checklist(report_id: int, allow_pending_revert: bool = True) -> None:
    """Inline action checklist for one report.

    Split into two visual sections:
      🔧 Automated remediation candidates  (action_type=executable)
      📋 Manual follow-up steps             (action_type=advisory)
    """
    actions = _fetch_actions_for_report(report_id)

    if actions.empty:
        st.markdown("**Recommended Steps**", unsafe_allow_html=True)
        st.caption("No actionable steps were emitted by the LLM for this report.")
        return

    executable = actions[actions["action_type"] == "executable"]
    advisory   = actions[actions["action_type"] != "executable"]

    if not executable.empty:
        st.markdown(
            f"**🔧 Automated remediation candidates** &nbsp; "
            f"*({len(executable)} step(s) — Approve to queue for the workflow)*",
            unsafe_allow_html=True,
        )
        for _, a in executable.iterrows():
            _render_one_action(a, allow_pending_revert=allow_pending_revert)

    if not advisory.empty:
        st.markdown(
            f"**📋 Manual follow-up steps** &nbsp; "
            f"*({len(advisory)} step(s) — operator performs manually; no auto-execution)*",
            unsafe_allow_html=True,
        )
        for _, a in advisory.iterrows():
            _render_one_action(a, allow_pending_revert=allow_pending_revert, advisory=True)


def _render_one_action(a, allow_pending_revert: bool = True, advisory: bool = False) -> None:
    action_id = int(a["action_id"])
    state     = a["state"]

    command       = _safe_str(a.get("command"))
    manual_steps  = _safe_str(a.get("manual_steps"))
    action_text   = _safe_str(a.get("action_text")) or "(no description)"
    approver_id   = _safe_str(a.get("approver_id"))
    decision_note = _safe_str(a.get("decision_note"))

    st.markdown(
        f"{_action_badge(state)} &nbsp; **Step {a['sequence_number']}**: {action_text}",
        unsafe_allow_html=True,
    )

    # Body content varies by type + state:
    #   executable + active state  -> show command (what will/did run)
    #   executable + rejected      -> show "if you want to do it yourself: <command>"
    #                                 plus manual_steps if LLM provided them
    #   advisory                   -> show manual_steps (if different from description)
    REJECTED_LIKE = {"rejected", "skipped"}
    if not advisory:
        if state in REJECTED_LIKE:
            # Operator opted not to auto-execute. Still surface how to do it manually.
            with st.container(border=True):
                st.markdown("**If you want to perform this manually:**")
                if command:
                    st.code(command, language="bash")
                if manual_steps and manual_steps != action_text:
                    st.markdown(f"*{manual_steps}*")
                if not command and not manual_steps:
                    st.caption("_(no command or step list available — refer to the action description above)_")
        elif command:
            st.code(command, language="bash")
    else:
        # Advisory path
        if manual_steps and manual_steps != action_text:
            with st.container(border=True):
                st.markdown("**Manual steps:**")
                st.markdown(manual_steps)

    if approver_id or decision_note or pd.notna(a.get("decided_at")):
        meta_bits = []
        if approver_id:
            meta_bits.append(f"by `{approver_id}`")
        if pd.notna(a.get("decided_at")):
            meta_bits.append(f"at {a['decided_at'].strftime('%H:%M:%S')}")
        if decision_note:
            meta_bits.append(f"note: *{decision_note}*")
        st.caption("&nbsp; " + " &middot; ".join(meta_bits))

    _render_action_controls(a, allow_pending_revert=allow_pending_revert, advisory=advisory)
    if not advisory:
        _render_execution_outcome(a)
    st.divider()


def _render_action_controls(a, allow_pending_revert: bool = True, advisory: bool = False) -> None:
    action_id = int(a["action_id"])
    state     = a["state"]

    # In-flight workflow states: locked, no buttons. Just show progress.
    if state in WORKFLOW_OWNED_STATES_UI:
        progress_label = {
            "approved":   "Queued — waiting for the remediation dispatcher to pick this up",
            "executing":  "Executing — LangGraph workflow is running the command",
            "verifying":  "Verifying — waiting for the post-execution metric check",
        }.get(state, state)
        st.info(f"⏳ **{state.upper()}** — {progress_label}")
        return

    # Any terminal state (workflow- or operator-produced): force an explicit
    # Reset before allowing any other transition. This prevents accidental
    # one-click reversal of a deliberate decision.
    if state in TERMINAL_STATES_UI:
        note_key = f"note_{action_id}"
        note = st.text_input("Decision note (optional)", key=note_key,
                             label_visibility="collapsed",
                             placeholder="Optional note before resetting")
        if st.button("↺ Reset to pending", key=f"reset_{action_id}",
                     use_container_width=True,
                     help="Clear this decision and put the action back in pending so you can re-decide"):
            _handle_decision_click(action_id, "pending", note)
            st.rerun()
        return

    # State is "pending" — show the decision buttons.
    note_key = f"note_{action_id}"
    note = st.text_input("Decision note (optional)", key=note_key,
                         label_visibility="collapsed",
                         placeholder="Optional note about your decision")

    if advisory:
        # Advisory: no Approve (nothing auto-executes). Just Done / Skip / Not applicable.
        btns = st.columns(3)
        if btns[0].button("✔ Done", key=f"done_{action_id}", use_container_width=True,
                          help="I've completed this manually"):
            _handle_decision_click(action_id, "done_manually", note)
            st.rerun()
        if btns[1].button("⊘ Skip", key=f"skip_{action_id}", use_container_width=True):
            _handle_decision_click(action_id, "skipped", note)
            st.rerun()
        if btns[2].button("✗ Not applicable", key=f"rej_{action_id}", use_container_width=True,
                          help="This recommendation doesn't apply to our context"):
            _handle_decision_click(action_id, "rejected", note)
            st.rerun()
        return

    # Executable + pending: full 4-button decision set.
    btns = st.columns(4)
    if btns[0].button("✓ Approve & execute", key=f"appr_{action_id}", use_container_width=True,
                      help="Approve and queue for the remediation workflow to execute"):
        _handle_decision_click(action_id, "approved", note)
        st.rerun()
    if btns[1].button("✗ Reject", key=f"rej_{action_id}", use_container_width=True,
                      help="Don't execute and don't track as done — discard this recommendation"):
        _handle_decision_click(action_id, "rejected", note)
        st.rerun()
    if btns[2].button("✔ Done", key=f"done_{action_id}", use_container_width=True,
                      help="I did this manually outside the system"):
        _handle_decision_click(action_id, "done_manually", note)
        st.rerun()
    if btns[3].button("⊘ Skip", key=f"skip_{action_id}", use_container_width=True,
                      help="Don't execute and don't track — skip this recommendation"):
        _handle_decision_click(action_id, "skipped", note)
        st.rerun()


def _render_execution_outcome(a) -> None:
    """For workflow-touched actions, show executor + verifier outcome.

    All cell access goes through _safe_str() so NaN/None never leaks into
    the rendered text. Panels are only shown if they have real content
    OR a real timestamp — manual-reject and pending actions show nothing.
    """
    state = a["state"]
    if state not in (
        "executing", "verifying",
        "verified", "unverified", "failed", "rejected",
    ):
        return

    exec_output     = _safe_str(a.get("execution_output"))
    verify_result   = _safe_str(a.get("verify_result"))
    has_executed_at = pd.notna(a.get("executed_at"))
    has_verified_at = pd.notna(a.get("verified_at"))

    # Executor outcome panel — only when something actually ran / was auto-rejected
    if exec_output or has_executed_at:
        exit_code = a.get("exit_code")
        try:
            exit_code_str = str(int(exit_code)) if pd.notna(exit_code) else "—"
        except (TypeError, ValueError):
            exit_code_str = "—"
        when = a["executed_at"].strftime("%H:%M:%S") if has_executed_at else "—"
        with st.expander(f"⚙ Execution outcome  ·  exit_code={exit_code_str}  ·  {when}", expanded=False):
            st.code(exec_output or "(no output captured)", language="text")

    # Verifier outcome panel — only when verifier actually ran
    if verify_result or has_verified_at:
        when = a["verified_at"].strftime("%H:%M:%S") if has_verified_at else "—"
        with st.expander(f"✓ Verification result  ·  {when}", expanded=False):
            st.write(verify_result or "(no verification details)")


st.set_page_config(
    page_title="Process Mining — Multi-Source RCA",
    page_icon=":mag_right:",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Kill the greyed-out "shadow" that Streamlit shows on every (auto-)rerun: hide the
# top-right "Running…" indicator and keep stale content at full opacity (no fade).
# Makes the ~2s Live Activity auto-refresh look seamless instead of flickering.
st.markdown(
    """
    <style>
      [data-testid="stStatusWidget"] { display: none !important; }
      [data-stale="true"] { opacity: 1 !important; transition: none !important; }
      div[data-testid="stElementContainer"][data-stale="true"] { opacity: 1 !important; }
    </style>
    """,
    unsafe_allow_html=True,
)

# APIFICATION: observability bootstrap is a server-side concern now — the UI
# container does not import app.observability (nor SQLAlchemy). The APPROVAL
# telemetry event is emitted server-side by /api/v1/actions/{id}/decide.


SEVERITY_COLOR = {
    "critical": "#ff4d4f",
    "high":     "#fa8c16",
    "normal":   "#52c41a",
    "info":     "#8c8c8c",
}


@st.cache_data(ttl=3, show_spinner=False)
def _query_df(view: str, **params) -> pd.DataFrame:
    # APIFICATION: the first positional argument used to be raw SQL; it is
    # now a named view registered in services/core/api/query_views.py. The
    # short-TTL cache still smooths per-tick fragment refreshes; writes call
    # st.cache_data.clear() so approvals/decisions show immediately.
    return api_client.query_df(view, params)


def _query_scalar(view: str, **params):
    # APIFICATION: named view; server returns a single-row/single-col dataframe.
    return api_client.query_scalar(view, params)


def _jsonb_list_values(jsonb_value) -> list:
    if not jsonb_value:
        return []
    if isinstance(jsonb_value, dict):
        return jsonb_value.get("values") or []
    if isinstance(jsonb_value, list):
        return jsonb_value
    return []


def _severity_badge(severity: str) -> str:
    color = SEVERITY_COLOR.get(severity, "#888")
    return f'<span style="background:{color};color:white;padding:2px 8px;border-radius:4px;font-size:0.85em;font-weight:600;">{severity.upper()}</span>'


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
st.sidebar.title("Process Mining")
PAGE = st.sidebar.radio(
    "View",
    # Live Activity first → it's the default landing view (real-time dashboard),
    # the most compelling entry point for a demo/business audience.
    ["Live Activity", "Overview", "Root-Cause Reports", "Pending Approvals", "Findings", "Events Explorer", "Cases", "Precedent Memory", "Source Configuration"],
    label_visibility="collapsed",
)

TENANT_ID = st.sidebar.number_input("Tenant ID", value=1, min_value=1, step=1)

APPROVER_ID = st.sidebar.text_input(
    "Approver name",
    value="anonymous",
    help="Identity recorded against your action decisions. Auth-free for now.",
)

st.sidebar.markdown("---")
if st.sidebar.button("Refresh", use_container_width=True):
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Overview
# ─────────────────────────────────────────────────────────────────────────────
def page_overview():
    st.title("Overview")

    # APIFICATION: raw counts via the query-view whitelist.
    n_events  = _query_scalar("overview.event_count",  t=TENANT_ID) or 0
    n_finds   = _query_scalar("overview.finding_count", t=TENANT_ID) or 0
    n_reports = _query_scalar("overview.report_count", t=TENANT_ID) or 0
    n_cases   = _query_scalar("overview.case_count",   t=TENANT_ID) or 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Events ingested",  f"{n_events:,}")
    c2.metric("Findings",          f"{n_finds:,}")
    c3.metric("RCA reports",       f"{n_reports:,}")
    c4.metric("Process cases",     f"{n_cases:,}")

    st.markdown("### Events by source")
    # APIFICATION: named view.
    src_df = _query_df("overview.events_by_source", t=TENANT_ID)
    if src_df.empty:
        st.info("No events ingested yet. Run `python app/runner.py --duration 6`.")
    else:
        st.dataframe(src_df, use_container_width=True, hide_index=True)

    st.markdown("### Findings by source and severity")
    # APIFICATION: named view.
    sev_df = _query_df("overview.findings_by_source_severity", t=TENANT_ID)
    if sev_df.empty:
        st.info("No findings yet.")
    else:
        pivot = sev_df.pivot(index="source_type", columns="severity", values="n").fillna(0).astype(int)
        st.dataframe(pivot, use_container_width=True)

    st.markdown("### Recent RCA reports")
    # APIFICATION: named view.
    recent = _query_df("overview.recent_reports", t=TENANT_ID)
    if recent.empty:
        st.info("No reports yet.")
    else:
        for _, row in recent.iterrows():
            badge = _severity_badge(row["severity"])
            ts = row["produced_at"].strftime("%Y-%m-%d %H:%M:%S") if row["produced_at"] else "—"
            snow_number = row.get("servicenow_number")
            snow_url    = row.get("servicenow_url")
            if snow_number and snow_url:
                snow_link = f"&nbsp; [{snow_number}]({snow_url})"
            elif snow_number:
                snow_link = f"&nbsp; {snow_number}"
            else:
                snow_link = ""
            st.markdown(
                f"{badge} &nbsp; **#{row['report_id']}** &nbsp; *{ts}* &nbsp; "
                f"(trigger finding #{row['trigger_finding_id']}){snow_link}  \n"
                f"{row['summary']}",
                unsafe_allow_html=True,
            )
            st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# Root-Cause Reports
# ─────────────────────────────────────────────────────────────────────────────
def page_reports():
    st.title("Root-Cause Reports")
    st.caption("LLM-produced cross-source hypotheses. Each is triggered by one finding and correlates evidence across sources.")

    sev_filter = st.multiselect("Severity", ["critical", "high", "normal"], default=["critical", "high"])
    if not sev_filter:
        sev_filter = ["critical", "high", "normal"]

    # APIFICATION: named view.
    reports = _query_df("reports.list_by_severity", t=TENANT_ID, sev=sev_filter)

    if reports.empty:
        st.info("No reports match the filter. Run the pipeline first or widen the severity filter.")
        return

    # Business-friendly ordering: surface the "hero" incident (highest severity +
    # most correlated sources) first and expand ONLY it; the rest stay collapsed.
    # Keeps the view clean when a run produces several incidents.
    _SEV_RANK = {"critical": 2, "high": 1, "normal": 0}

    def _sources_of(r):
        p = r.get("payload") or {}
        srcs = p.get("incident_sources") if isinstance(p, dict) else None
        return list(srcs) if isinstance(srcs, list) else []

    def _score(r):
        ts_key = r["produced_at"].timestamp() if pd.notna(r["produced_at"]) else 0.0
        return (_SEV_RANK.get(str(r["severity"]), 0), len(_sources_of(r)), ts_key)

    ranked = sorted((r for _, r in reports.iterrows()), key=_score, reverse=True)
    hero = ranked[0]
    n_hero_srcs = len(_sources_of(hero))
    others = len(ranked) - 1
    st.markdown(
        f"**Primary incident:** {hero['severity'].upper()} across **{n_hero_srcs} source(s)**"
        + (f" &nbsp;·&nbsp; +{others} related incident(s) below" if others else "")
    )

    for idx, row in enumerate(ranked):
        badge = _severity_badge(row["severity"])
        ts = row["produced_at"].strftime("%Y-%m-%d %H:%M:%S") if row["produced_at"] else "—"
        triggers = _jsonb_list_values(row["trigger_finding_ids"]) or [row["trigger_finding_id"]]
        triggers_label = f"{len(triggers)} finding(s)" if len(triggers) > 1 else f"finding #{row['trigger_finding_id']}"
        n_row_srcs = len(_sources_of(row))
        star = "⭐ " if idx == 0 else ""
        src_label = f" · {n_row_srcs} sources" if n_row_srcs else ""
        with st.expander(
            f"{star}#{row['report_id']} · {row['severity'].upper()} · {ts} · {triggers_label}{src_label}",
            expanded=(idx == 0),
        ):
            confidence = _extract_confidence(row.get("payload"))
            conf_badge = _confidence_badge(confidence)
            st.markdown(
                f"{badge} &nbsp; {conf_badge} &nbsp; *Investigator:* `{row['investigator_class']}`",
                unsafe_allow_html=True,
            )

            # Coverage summary (which pillars contributed)
            payload = row.get("payload") or {}
            coverage = payload.get("coverage") if isinstance(payload, dict) else None
            if isinstance(coverage, dict):
                present = coverage.get("pillars_present") or []
                absent  = coverage.get("pillars_absent") or []
                if present or absent:
                    parts = []
                    if present:
                        parts.append(f"present: **{', '.join(present)}**")
                    if absent:
                        parts.append(f"absent: ~~{', '.join(absent)}~~")
                    st.caption("Coverage — " + " &nbsp;·&nbsp; ".join(parts))

            if len(triggers) > 1:
                st.markdown(
                    f"**Incident** — grouped {len(triggers)} findings: "
                    f"`{', '.join(f'#{t}' for t in triggers)}`. "
                    f"Primary: `#{row['trigger_finding_id']}`"
                )
            else:
                st.markdown(f"**Trigger finding:** `#{row['trigger_finding_id']}`")

            # ServiceNow back-link (set by ServiceNowReportSink after creating
            # or finding the ticket). Mock-mode reports get a file:// link.
            snow_number = row.get("servicenow_number")
            snow_url    = row.get("servicenow_url")
            if snow_number:
                if snow_url:
                    st.markdown(
                        f"**ServiceNow:** [{snow_number}]({snow_url})",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(f"**ServiceNow:** {snow_number}")
            else:
                st.caption("ServiceNow ticket not yet linked — re-run pipeline to populate.")

            st.markdown(f"**Summary**  \n{row['summary']}")

            chain = (row["evidence_chain"] or {}).get("entries") if isinstance(row["evidence_chain"], dict) else None
            if chain:
                st.markdown("**Evidence chain**")
                ev_df = pd.DataFrame([
                    {
                        "Role":      e.get("role") or e.get("tier"),
                        "Source":    e.get("source") or e.get("source_type"),
                        "Signal":    e.get("signal") or e.get("subject") or e.get("observation"),
                        "Timestamp": e.get("timestamp"),
                    }
                    for e in chain
                ])
                st.dataframe(ev_df, use_container_width=True, hide_index=True)

            ck = row["correlation_keys"] or {}
            payload = row["payload"] or {}
            related_events   = _jsonb_list_values(row["related_event_ids"])
            related_findings = _jsonb_list_values(row["related_finding_ids"])

            cols = st.columns(3)
            cols[0].markdown(f"**Time anchor:** `{ck.get('time_anchor', '—')}`")
            cols[1].markdown(f"**Hosts:** `{', '.join(ck.get('infra_hosts') or []) or '—'}`")
            cols[2].markdown(f"**Cases:** {len(ck.get('case_ids') or [])}")

            cols2 = st.columns(2)
            cols2[0].markdown(f"**Related events:** {len(related_events)}")
            cols2[1].markdown(f"**Related findings:** {len(related_findings)}")

            llm_raw = payload.get("llm_raw") if isinstance(payload, dict) else None
            if isinstance(llm_raw, dict):
                factors = llm_raw.get("contributing_factors") or []
                if factors:
                    st.markdown("**Contributing factors**")
                    for f in factors:
                        st.markdown(f"- {f}")
                unc = llm_raw.get("uncertainties") or []
                if unc:
                    st.markdown("**Uncertainties**")
                    for u in unc:
                        st.markdown(f"- {u}")

            # Low-confidence warning above the action controls
            if confidence is not None and confidence < 0.7:
                st.warning(
                    f"This RCA hypothesis has confidence **{confidence:.2f}**. "
                    "Review the evidence chain carefully before approving any action."
                )

            # POINT 19 (Task #19): similar PAST incidents (precedent) + their fixes.
            # Read straight from the payload the investigator saved — no extra
            # queries. Section is hidden when a report has no precedents.
            precedents = payload.get("historical_precedents") if isinstance(payload, dict) else None
            if precedents:
                st.markdown(
                    f"**📚 Similar past incidents** &nbsp; "
                    f"*({len(precedents)} found — precedent for this incident)*",
                    unsafe_allow_html=True,
                )
                for p in precedents:
                    ticket   = p.get("ticket") or "(no number)"
                    system   = p.get("system") or "—"
                    conf     = p.get("confidence")
                    conf_pct = f"{int(conf * 100)}%" if isinstance(conf, (int, float)) else "—"
                    resolved = str(p.get("resolved_at") or "")[:10]
                    outcome_badge = _outcome_badge(p.get("outcome"))   # POINT 19
                    with st.container(border=True):
                        st.markdown(
                            f"**{ticket}** &nbsp; {outcome_badge} &nbsp;·&nbsp; "
                            f"system: `{system}` &nbsp;·&nbsp; "
                            f"confidence: {conf_pct} &nbsp;·&nbsp; resolved {resolved or '—'}",
                            unsafe_allow_html=True,
                        )
                        if p.get("what"):
                            st.markdown(f"*What happened:* {p.get('what')}")
                        if p.get("resolution"):
                            st.markdown(f"*How it was resolved:* {p.get('resolution')}")

            # Interactive Recommended Steps section (Phase B)
            _render_action_checklist(report_id=row["report_id"])

            st.markdown("**Raw payload**")
            st.json({"correlation_keys": ck, "payload": payload}, expanded=False)


# ─────────────────────────────────────────────────────────────────────────────
# Pending Approvals (cross-report queue)
# ─────────────────────────────────────────────────────────────────────────────
def page_pending_approvals():
    st.title("Pending Approvals")
    st.caption("Recommended steps from LLM RCA reports that still need a decision. Approve to commit to executing manually; reject if not appropriate; mark done after completing yourself.")

    show_all = st.checkbox("Show all actions (not just pending)", value=False)
    state_filter = ["pending"] if not show_all else list(VALID_STATES)

    # APIFICATION: named view.
    queue = _query_df("approvals.queue", t=TENANT_ID, states=state_filter)

    if queue.empty:
        if show_all:
            st.info("No remediation actions yet — run the pipeline to populate.")
        else:
            st.success("No pending actions. All decisions made.")
        return

    # Group by report for context
    by_report: dict[int, list[dict]] = {}
    for _, row in queue.iterrows():
        by_report.setdefault(row["report_id"], []).append(row.to_dict())

    st.markdown(f"**{len(by_report)} report(s)** with {len(queue)} action(s) to review")

    for report_id, action_rows in by_report.items():
        sample = action_rows[0]
        sev_badge = _severity_badge(sample["severity"])
        conf = _extract_confidence(sample.get("payload"))
        conf_badge = _confidence_badge(conf)
        snow_link = ""
        if sample.get("servicenow_number") and sample.get("servicenow_url"):
            snow_link = f" &nbsp; · [{sample['servicenow_number']}]({sample['servicenow_url']})"
        st.markdown(
            f"### Report #{report_id} &nbsp; {sev_badge} &nbsp; {conf_badge}{snow_link}",
            unsafe_allow_html=True,
        )
        st.caption(sample["summary"])
        if conf is not None and conf < 0.7:
            st.warning(f"Confidence {conf:.2f} — review evidence before approving.")
        _render_action_checklist(report_id=report_id)


# ─────────────────────────────────────────────────────────────────────────────
# Findings
# ─────────────────────────────────────────────────────────────────────────────
def page_findings():
    st.title("Findings (Tier-1)")
    st.caption("Per-source analyzer findings. Each finding can trigger a tier-2 investigation if it matches investigator triggers.")

    # APIFICATION: named views.
    src_options = _query_df("findings.source_options", t=TENANT_ID)["source_type"].tolist()
    src_filter = st.multiselect("Source type", src_options, default=src_options)
    sev_filter = st.multiselect("Severity", ["critical", "high", "normal"], default=["critical", "high"])

    if not src_filter:
        src_filter = src_options
    if not sev_filter:
        sev_filter = ["critical", "high", "normal"]

    findings = _query_df(
        "findings.list_by_filter",
        t=TENANT_ID, src=src_filter, sev=sev_filter,
    )

    if findings.empty:
        st.info("No findings match the filter.")
        return

    st.markdown(f"**{len(findings)} finding(s)**")

    for _, row in findings.iterrows():
        badge = _severity_badge(row["severity"])
        ts = row["produced_at"].strftime("%Y-%m-%d %H:%M:%S") if row["produced_at"] else "—"
        with st.expander(
            f"#{row['finding_id']} · {row['source_type']} · {row['subject_key']} · {row['severity'].upper()} · {ts}",
            expanded=False,
        ):
            st.markdown(f"{badge} &nbsp; **{row['source_type']}** — `{row['subject_key']}`", unsafe_allow_html=True)
            st.markdown(f"**Observation**  \n{row['observation']}")

            cols = st.columns(3)
            cols[0].markdown(f"**Servers:** `{', '.join(_jsonb_list_values(row['server_ids'])) or '—'}`")
            cols[1].markdown(f"**Cases:** {len(_jsonb_list_values(row['case_ids']))}")
            cols[2].markdown(f"**Actors:** {len(_jsonb_list_values(row['actor_ids']))}")

            cols2 = st.columns(2)
            cols2[0].markdown(
                f"**Window:** `{row['time_min']}` → `{row['time_max']}`"
            )
            processed = pd.notna(row["processed_at"])
            cols2[1].markdown(
                f"**Processed:** {'yes' if processed else 'no'} "
                f"({row['processed_at'].strftime('%H:%M:%S') if processed else '—'})"
            )

            st.markdown("**Payload**")
            st.json(row["payload"] or {}, expanded=False)


# ─────────────────────────────────────────────────────────────────────────────
# Events Explorer
# ─────────────────────────────────────────────────────────────────────────────
def page_events():
    st.title("Events Explorer")
    st.caption("Raw EVENT_LOG rows from every source.")

    # APIFICATION: named views. The dynamic-filter logic lives server-side now
    # (see services/core/api/query_views.py :: _events_explorer). The client
    # sends the raw filter text; the view builder decides whether to add the
    # optional ILIKE clause.
    src_options = _query_df("events.source_options", t=TENANT_ID)["source_type"].tolist()

    col1, col2, col3 = st.columns([2, 2, 1])
    src_filter = col1.multiselect("Source type", src_options, default=src_options)
    server_filter = col2.text_input("Server ID contains", value="")
    limit = col3.number_input("Max rows", value=200, min_value=10, max_value=5000, step=50)

    if not src_filter:
        src_filter = src_options

    events = _query_df(
        "events.explorer",
        t=TENANT_ID, src=src_filter, lim=int(limit),
        srv=server_filter.strip(),
    )

    if events.empty:
        st.info("No events match the filter.")
        return

    st.markdown(f"**{len(events)} event(s)** (most recent first)")

    show_meta = st.checkbox("Show metadata_json column", value=False)
    if not show_meta:
        events = events.drop(columns=["metadata_json"])
    st.dataframe(events, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# Cases
# ─────────────────────────────────────────────────────────────────────────────
def page_cases():
    st.title("Process Cases")
    st.caption("Auto-created from Appian integration_trace Process IDs. Each case has zero or more events.")

    # APIFICATION: named view.
    cases = _query_df("cases.list", t=TENANT_ID)

    if cases.empty:
        st.info("No cases yet.")
        return

    st.markdown(f"**{len(cases)} case(s)**")
    st.dataframe(cases, use_container_width=True, hide_index=True)

    st.markdown("---")
    selected_ref = st.selectbox(
        "Drill into case (by reference):",
        options=[""] + cases["case_reference_id"].dropna().astype(str).tolist(),
    )
    if selected_ref:
        # APIFICATION: named view.
        events = _query_df("cases.events_for_ref", t=TENANT_ID, ref=selected_ref)
        if events.empty:
            st.info(f"No events for case {selected_ref}.")
        else:
            st.markdown(f"### Events for case `{selected_ref}` ({len(events)})")
            for _, row in events.iterrows():
                ts = row["timestamp"].strftime("%H:%M:%S") if row["timestamp"] else "—"
                with st.expander(f"{ts} · {row['source_type']} · {row['activity_name']}", expanded=False):
                    st.markdown(
                        f"**actor:** `{row['actor_id'] or '—'}`  "
                        f"**server:** `{row['server_id'] or '—'}`  "
                        f"**stage:** `{row['lifecycle_stage'] or '—'}`"
                    )
                    st.json(row["metadata_json"] or {}, expanded=False)


# ─────────────────────────────────────────────────────────────────────────────
# Precedent Memory (POINT 19 — the FAISS vector index, browse + search)
# ─────────────────────────────────────────────────────────────────────────────
def page_precedent_memory():
    # APIFICATION: Precedent Memory is entirely server-driven now. The UI
    # container has neither FAISS nor SQLAlchemy — it just renders whatever
    # /api/v1/precedent/{status,search} returns.
    st.title("Precedent Memory")
    st.caption(
        "The vector index (FAISS) behind \"have we seen this before?\". It stores the embedding "
        "of each PAST incident's problem text, keyed by incident_id; the fix, outcome and details "
        "live in Postgres and are joined in below."
    )

    try:
        status = api_client.precedent_status(tenant_id=int(TENANT_ID))
    except Exception as e:
        st.error(f"Could not load the vector index: {e}")
        return

    counts = status.get("counts") or {"approved": 0, "rejected": 0, "unknown": 0}
    c1, c2, c3 = st.columns(3)
    c1.metric("Backend", (status.get("backend") or "").upper())
    c2.metric("Vectors indexed", status.get("vectors_indexed") or 0)
    c3.metric("Approved / Rejected / Unknown",
              f"{counts.get('approved', 0)} / {counts.get('rejected', 0)} / {counts.get('unknown', 0)}")

    st.divider()
    st.subheader("Search the precedent memory")
    query = st.text_input("Describe a problem", placeholder="e.g. credit bureau calls timing out")
    k = st.slider("Results", 1, 10, 5)
    if query:
        try:
            result = api_client.precedent_search(tenant_id=int(TENANT_ID), query=query, k=k)
            hits = result.get("hits") or []
            st.caption(f"semantic search · embedder mode: {result.get('embedder_mode', 'unknown')}")
        except Exception as e:
            st.error(f"Search failed: {e}")
            hits = []
        if not hits:
            st.info("No matches.")
        for h in hits:
            badge = _outcome_badge(h.get("outcome"))
            with st.container(border=True):
                st.markdown(
                    f"**{h.get('number') or h['id']}** &nbsp; {badge} &nbsp;·&nbsp; "
                    f"similarity: {h.get('score', 0.0):.2f} &nbsp;·&nbsp; "
                    f"system: `{h.get('cmdb_ci') or '—'}`",
                    unsafe_allow_html=True,
                )
                if h.get("short_description"):
                    st.markdown(f"*Problem:* {h['short_description']}")
                if h.get("close_notes"):
                    st.markdown(f"*Resolution:* {h['close_notes']}")

    st.divider()
    incidents = status.get("incidents") or []
    with st.expander(f"All indexed incidents ({len(incidents)})", expanded=False):
        if incidents:
            df = pd.DataFrame(incidents)
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.info("No indexed incidents for this tenant.")


# ─────────────────────────────────────────────────────────────────────────────
# POINT 21 (Task #21): Live Activity — real-time pipeline-stage timeline
# ─────────────────────────────────────────────────────────────────────────────
# Stage colors mirror the LLD deck legend so the story reads consistently.
_STAGE_COLOR = {
    "INGEST": "#2980b9", "DETECT": "#2980b9",            # ingestion & detection
    "CORRELATE": "#5b4e9c", "DIAGNOSE": "#5b4e9c",        # AI diagnosis
    "SNOW_CREATE": "#d35400", "SNOW_RESOLVE": "#d35400",  # ServiceNow (ITSM)
    "APPROVAL": "#d68910",                                # human approval
    "VALIDATE": "#c0392b", "EXECUTE": "#c0392b", "VERIFY": "#c0392b",  # remediation
    "JIRA_CREATE": "#1f9d57",                             # Jira audit
    "LEARN": "#16a085",                                   # precedent learning
}
_STATUS_ICON = {"started": "⏳", "progress": "🔹", "succeeded": "✅",
                "failed": "❌", "skipped": "⏭️"}
# POINT 21 (Task #21): Claude-thinking-style phrases for the live "working" banner.
_WORKING = {
    "INGEST": "Collecting live telemetry", "DETECT": "Detecting anomalies",
    "CORRELATE": "Correlating signals across systems into incidents",
    "DIAGNOSE": "Diagnosing root cause using AI + proven history",
    "SNOW_CREATE": "Raising the incident in ServiceNow", "APPROVAL": "Awaiting your approval",
    "VALIDATE": "Safety-checking the fix", "EXECUTE": "Applying the fix",
    "VERIFY": "Confirming the fix worked", "JIRA_CREATE": "Logging the audit record in Jira",
    "SNOW_RESOLVE": "Closing the incident in ServiceNow", "LEARN": "Learning from the outcome",
}
_META_KEYS = ("sources", "trigger", "system", "inc", "jira_key", "status", "severity",
              "precedents", "confidence", "model", "exit_code", "passed", "outcome",
              "duration_ms", "count")


def _stage_chip(stage: str) -> str:
    c = _STAGE_COLOR.get(stage, "#888")
    return (f'<span style="background:{c};color:#fff;padding:1px 7px;border-radius:4px;'
            f'font-size:0.78em;font-weight:600;">{stage}</span>')


def _event_line(r) -> str:
    icon = _STATUS_ICON.get(r["status"], "•")
    ts = r["created_at"].strftime("%Y-%m-%d %H:%M:%S") if pd.notna(r["created_at"]) else ""
    text_bit = r["step"] or r["message"] or ""
    meta = r["meta"] if isinstance(r["meta"], dict) else {}
    chips = []
    for k in _META_KEYS:
        v = meta.get(k)
        if v is None:
            continue
        if isinstance(v, list):           # e.g. sources=[mule, prometheus]
            v = ", ".join(map(str, v))
        chips.append(f"{k}={v}")
    tail = (f" <span style='color:#888;font-size:0.8em'>· {' · '.join(chips)}</span>"
            if chips else "")
    return f"{icon} <code>{ts}</code> {_stage_chip(r['stage'])} {text_bit}{tail}"


# POINT 21 (Task #21): per-incident status -> (color, label) for the colored box + legend.
_INCIDENT_STATUS = {
    "closed":   ("#1f9d57", "Closed — resolved & learned"),
    "awaiting": ("#d68910", "Awaiting your approval"),
    "failed":   ("#c0392b", "Failed — needs attention"),
    "working":  ("#2980b9", "In progress"),
    "other":    ("#7f8c8c", "Open / idle"),
}


def _incident_status_key(sub, corr=None, awaiting_corrs=frozenset(), inprogress_corrs=frozenset()) -> str:
    """Classify one incident (priority: closed > failed > awaiting > working > open).

    DB-derived sets (more reliable than events alone):
      awaiting_corrs   = has a PENDING EXECUTABLE action (needs your approval)
      inprogress_corrs = has an action in approved/executing/verifying (running)"""
    succeeded = set(sub[sub["status"] == "succeeded"]["stage"])
    failed = set(sub[sub["status"] == "failed"]["stage"])
    appr = sub[sub["stage"] == "APPROVAL"]
    appr_unfinished = (not appr.empty) and ("succeeded" not in set(appr["status"]))
    last_status = str(sub.iloc[-1]["status"])
    if "LEARN" in succeeded:
        return "closed"
    if failed:
        return "failed"
    # "working" (a fix is actively running) outranks "awaiting" so an incident turns
    # blue "In progress" the moment you approve — even if it still has OTHER pending
    # executable actions (the LLM often proposes several fixes per incident).
    if (corr in inprogress_corrs) or last_status in ("started", "progress"):
        return "working"
    if (corr in awaiting_corrs) or appr_unfinished:
        return "awaiting"
    return "other"


def page_live_activity():
    st.subheader("Live Activity — real-time pipeline")
    st.caption("What the pipeline is doing right now. Each incident is one thread: "
               "diagnose → ServiceNow ticket → human approval → validate → execute → "
               "verify → Jira → resolve → learn. Auto-refreshes.")
    c1, c2 = st.columns([1, 1])
    interval = c1.slider("Refresh every (seconds)", 1, 10, 2)
    # History filter: default to a recent window (clean, live-focused). Widen to
    # review past incidents — the timeline is retained (not wiped on reset).
    window = c2.selectbox(
        "Show activity from",
        ["Last 15 minutes", "Last hour", "Last 24 hours", "Last 7 days", "Last 30 days", "All time"],
        index=0,
    )
    # Values are from a whitelisted dict (not user free-text) → safe to embed in SQL.
    _WINDOW = {
        "Last 15 minutes": "15 minutes", "Last hour": "1 hour", "Last 24 hours": "24 hours",
        "Last 7 days": "7 days", "Last 30 days": "30 days",
    }
    st.caption(f"Showing **{window.lower()}** · live view defaults to recent activity — "
               "widen the range to review past incidents (timeline history is retained).")

    @st.fragment(run_every=interval)
    def _live():
        # Show a loading spinner only on DELIBERATE loads (first open / window change),
        # not on every silent 2s auto-refresh tick — so there's clear feedback when a
        # (slower) fresh query runs, without reintroducing per-tick flicker.
        _loading = st.session_state.get("_la_win") != window
        st.session_state["_la_win"] = window
        _spin = st.spinner("Loading timeline…") if _loading else nullcontext()
        with _spin:
            # Standing call-to-action — executable fixes waiting on a human (shown
            # regardless of window, so the user always knows something is pending).
            # APIFICATION: named views.
            pend = _query_df("live.pending_exec_count", t=TENANT_ID)
            n_pending = int(pend.iloc[0]["n"]) if not pend.empty else 0

            _win = _WINDOW.get(window)
            # Step 1 — which incidents had activity in the selected window.
            recent = _query_df(
                "live.recent_correlations",
                t=TENANT_ID, **({"win": _win} if _win else {}),
            )
            corrs = [c for c in recent["correlation_id"].tolist() if c] if not recent.empty else []
            # Step 2 — pull the FULL timeline for those incidents (not just events inside
            # the window), so a closed/older incident still shows all its stages.
            df = _query_df(
                "live.timeline_for_corrs",
                t=TENANT_ID, corrs=corrs,
            ) if corrs else None

        if n_pending:
            st.warning(f"⏳ **{n_pending} executable fix(es) awaiting your approval** — "
                       f"open the **Pending Approvals** page to approve and run them.")
        if not corrs or df is None or df.empty:
            st.info(f"No pipeline activity in {window.lower()}.")
            return

        # POINT 21 (Task #21): live "working…" banner (Claude-thinking style). Animated
        # dots cycle each auto-refresh; the phrase reflects the newest in-flight stage.
        st.session_state["_la_tick"] = st.session_state.get("_la_tick", 0) + 1
        dots = "." * (1 + st.session_state["_la_tick"] % 3)
        newest = df.iloc[0]
        _stg, _sts = newest["stage"], str(newest["status"])
        if _sts in ("started", "progress"):
            st.markdown(f"#### 🧠 {_WORKING.get(_stg, 'Working')}{dots}")
        else:
            st.markdown(f"#### 💤 Watching for activity{dots}")

        inc = df[df["correlation_id"].notna()]
        incidents = list(dict.fromkeys(inc["correlation_id"].tolist()))  # most-recent first

        # POINT 21 (Task #21): which incidents have a PENDING EXECUTABLE action -> "awaiting".
        # APIFICATION: named view.
        aw = _query_df("live.awaiting_corrs", t=TENANT_ID)
        awaiting_corrs = set(aw["corr"].dropna().tolist()) if not aw.empty else set()

        # POINT 21 (Task #21): incidents whose action is approved/running -> "In progress"
        # (covers the gap between Approve and the first remediation stage firing).
        # APIFICATION: named view.
        ip = _query_df("live.inprogress_corrs", t=TENANT_ID)
        inprogress_corrs = set(ip["corr"].dropna().tolist()) if not ip.empty else set()

        # Business-friendly ordering: running incidents first, then awaiting-approval,
        # then resolved/idle — each group stays most-recent-first (sorted() is stable).
        def _incident_rank(c):
            if c in inprogress_corrs: return 0
            if c in awaiting_corrs:   return 1
            return 2
        incidents = sorted(incidents, key=_incident_rank)

        # RCA narrative per incident — shown inline at the top of each expander so a
        # viewer sees the root cause without leaving the page. Reports link to the
        # timeline via payload->>'timeline_correlation_id'; keep the latest per corr.
        rca_by_corr = {}
        if incidents:
            # APIFICATION: named view.
            _rca = _query_df("live.rca_by_corr", t=TENANT_ID, corrs=incidents)
            for _, _r in _rca.iterrows():
                rca_by_corr.setdefault(_r["corr"], _r)

        # POINT 21 (Task #21): color legend for the incident boxes.
        legend = "  ".join(
            f"<span style='background:{c};color:#fff;padding:1px 8px;border-radius:4px;font-size:0.78em'>{lbl}</span>"
            for c, lbl in _INCIDENT_STATUS.values())
        st.markdown(f"**{len(incidents)} incident timeline(s)** &nbsp; — &nbsp; {legend}",
                    unsafe_allow_html=True)

        for i, corr in enumerate(incidents[:12]):
            sub = inc[inc["correlation_id"] == corr].sort_values("event_id")
            # Show only the MOST RECENT occurrence of this incident: slice from the last
            # DIAGNOSE 'started' onward. A scenario re-run reuses the same correlation_id,
            # so without this the box would stack multiple full cycles ("showing twice").
            _starts = sub[(sub["stage"] == "DIAGNOSE") & (sub["status"] == "started")]
            if not _starts.empty:
                sub = sub[sub["event_id"] >= _starts.iloc[-1]["event_id"]]
            key = _incident_status_key(sub, corr, awaiting_corrs, inprogress_corrs)
            color, label = _INCIDENT_STATUS[key]
            # colored status box (the expander itself can't be tinted, so this strip is the "box")
            st.markdown(
                f"<div style='background:{color};color:#fff;padding:7px 12px;border-radius:6px;"
                f"margin:10px 0 0 0;font-weight:600;'>{label} &nbsp;·&nbsp; "
                f"<span style='font-weight:400;opacity:0.9'>{corr}</span></div>",
                unsafe_allow_html=True)
            with st.expander("Root cause & stages", expanded=(key in ("working", "awaiting") or i == 0)):
                rep = rca_by_corr.get(corr)
                if rep is not None:
                    snow = f" · ServiceNow {rep['servicenow_number']}" if rep.get("servicenow_number") else ""
                    st.markdown(f"**Root cause** — {str(rep['severity']).upper()}{snow}")
                    st.caption(rep["summary"] or "—")
                    st.markdown("<hr style='margin:6px 0'>", unsafe_allow_html=True)
                for _, r in sub.iterrows():
                    st.markdown(_event_line(r), unsafe_allow_html=True)

        glob = df[df["correlation_id"].isna()].head(20)
        if not glob.empty:
            st.markdown("---")
            st.markdown("**System activity** (ingest / detect / correlate)")
            for _, r in glob.iterrows():
                st.markdown(_event_line(r), unsafe_allow_html=True)

    _live()


# ─────────────────────────────────────────────────────────────────────────────
# Source Configuration — key/value settings per source type.
# UI → config-service → core-service (aggregator) → source_config table.
# ─────────────────────────────────────────────────────────────────────────────
def _source_types() -> dict[str, dict]:
    # SOURCE-CONFIG: catalog is owned by config-service; no local copy.
    return {t["value"]: t for t in api_client.list_source_types()}


def _clear_source_config_form():
    st.session_state["cfg_key"] = ""
    st.session_state["cfg_value"] = ""


def _save_source_config():
    key = st.session_state.get("cfg_key", "").strip()
    value = st.session_state.get("cfg_value", "").strip()
    if not key or not value:
        st.session_state["cfg_flash"] = ("error", "Key and Value are both required.")
        return
    try:
        api_client.save_source_config(
            tenant_id=int(TENANT_ID),
            source_type=st.session_state["cfg_source_type"],
            key=key,
            value=value,
        )
    except Exception as e:
        st.session_state["cfg_flash"] = ("error", f"Save failed: {e}")
        return
    st.session_state["cfg_flash"] = ("success", f"Saved {st.session_state['cfg_source_type']} / {key}.")
    _clear_source_config_form()


def _delete_source_config(source_type: str, key: str):
    try:
        api_client.delete_source_config(tenant_id=int(TENANT_ID), source_type=source_type, key=key)
    except Exception as e:
        st.session_state["cfg_flash"] = ("error", f"Delete failed: {e}")
        return
    st.session_state["cfg_flash"] = ("success", f"Deleted {source_type} / {key}.")


def page_source_configuration():
    st.title("Source Configuration")
    st.caption("Key = component (e.g. `vote`), Value = where to read it from (e.g. its log URL). "
               "Saving an existing key overwrites its value.")

    try:
        types = _source_types()
    except Exception as e:
        st.error(f"Could not load source types from config-service: {e}")
        return

    with st.container(border=True):
        st.selectbox(
            "Source Type",
            options=list(types),
            format_func=lambda v: types[v]["label"],
            key="cfg_source_type",
        )
        hint = types[st.session_state["cfg_source_type"]]
        st.text_input("Key", key="cfg_key", placeholder=hint["key_hint"])
        st.text_input("Value", key="cfg_value", placeholder=hint["value_hint"])
        c1, c2, _ = st.columns([1, 1, 6])
        c1.button("OK", type="primary", use_container_width=True, on_click=_save_source_config)
        c2.button("Cancel", use_container_width=True, on_click=_clear_source_config_form)

    flash = st.session_state.pop("cfg_flash", None)
    if flash:
        getattr(st, flash[0])(flash[1])

    st.subheader("Saved configuration")
    try:
        rows = api_client.list_source_configs(tenant_id=int(TENANT_ID))
    except Exception as e:
        st.error(f"Could not load configuration: {e}")
        return
    if not rows:
        st.info("No configuration saved for this tenant yet.")
        return
    df = pd.DataFrame(rows)[["source_type", "key", "value", "updated_at"]]
    # Rows under a retired type name show the raw value so they can be spotted and deleted.
    df["source_type"] = df["source_type"].map(lambda v: types[v]["label"] if v in types else v)
    df.columns = ["Source Type", "Key", "Value", "Updated"]
    st.dataframe(df, use_container_width=True, hide_index=True)

    # SOURCE-CONFIG: delete one saved entry.
    pairs = [(r["source_type"], r["key"]) for r in rows]
    d1, d2 = st.columns([5, 1])
    target = d1.selectbox("Delete entry", options=pairs, format_func=lambda p: f"{p[0]} / {p[1]}",
                          key="cfg_delete_target")
    d2.button("Delete", use_container_width=True, on_click=_delete_source_config, args=target)


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────
PAGES = {
    "Overview":           page_overview,
    "Live Activity":      page_live_activity,
    "Root-Cause Reports": page_reports,
    "Pending Approvals":  page_pending_approvals,
    "Findings":           page_findings,
    "Events Explorer":    page_events,
    "Cases":              page_cases,
    "Precedent Memory":   page_precedent_memory,
    "Source Configuration": page_source_configuration,
}
PAGES[PAGE]()
