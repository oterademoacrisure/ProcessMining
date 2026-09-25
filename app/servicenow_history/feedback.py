"""POINT 19 (Task #19) — Step 7: capture our OWN resolved incidents as precedent.

Closes the feedback loop. When a remediation finishes, turn the incident + the
fix that ran into a historical_incident precedent (and index it), so future RCAs
learn from our own outcomes — both what worked and what didn't.

  remediation final state -> precedent outcome:
    verified              -> approved   (fix applied AND verified to work)
    unverified | failed   -> rejected   (fix tried, did NOT work -> "avoid this")
    rejected (allow-list) -> skipped    (never ran -> nothing to learn)

Direct-capture: writes straight into historical_incident + the vector index, so
it works in any servicenow_mode (the demo runs in mock). Idempotent per report
(sys_id = serverops-report-<id>) — re-running a workflow updates, never dupes.
The captured precedent reuses the SAME correlation_id recipe as the ServiceNow
sink, so a recurrence of the same problem matches it as an exact recurrence (0.95).

Fail-safe: any error here is logged and swallowed — remediation is never blocked.

(Real-mode note: when servicenow_mode=real you may ALSO close the ticket in
ServiceNow and let the history sync pull it back; dedupe by report would be a
future refinement. For now direct-capture is the single, uniform mechanism.)
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import HistoricalIncident, RemediationAction, RootCauseReport
from app.db.session import SessionLocal
from app.servicenow_history.indexer import problem_text
from app.servicenow_history.loader import load_incidents

log = logging.getLogger(__name__)

# remediation final_state -> precedent outcome (None = don't capture)
_STATE_TO_OUTCOME = {
    "verified":   "approved",
    "unverified": "rejected",
    "failed":     "rejected",
    "rejected":   None,        # auto-rejected by allow-list: never executed
}


def _slug(value: Any) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return s[:60] if s else "unknown"


def _correlation_id(report: RootCauseReport) -> str:
    """Mirror the ServiceNow sink's recipe so a recurrence matches at 0.95."""
    ck = report.correlation_keys or {}
    primary = (report.payload or {}).get("primary_trigger") or {}
    tenant = ck.get("tenant_id", report.tenant_id)
    return (
        f"rca-t{tenant}-{_slug(primary.get('source_type'))}-"
        f"{_slug(primary.get('subject'))}-{_slug(report.severity)}"
    )[:100]


def capture_precedent(action_id: int, final_state: str, config: dict | None = None) -> int | None:
    """Capture a finished remediation as a precedent. Returns incident_id or None."""
    config = config or {}
    outcome = _STATE_TO_OUTCOME.get(final_state)
    if outcome is None:
        return None
    if not bool(config.get("precedent_feedback", True)):
        return None

    try:
        with SessionLocal() as session:
            action = session.get(RemediationAction, action_id)
            if action is None:
                return None
            report = session.get(RootCauseReport, action.report_id)
            if report is None:
                return None

            primary = (report.payload or {}).get("primary_trigger") or {}
            tenant_id = report.tenant_id
            report_id = report.report_id
            system = primary.get("subject")     # the affected system, e.g. credit-bureau-prod

            short_desc = (report.summary or primary.get("observation") or "Resolved incident")[:400]
            # resolution = the action that ran + how it verified
            bits = [action.action_text or ""]
            if action.command:
                bits.append(f"Command: {action.command}")
            if action.verify_result:
                bits.append(f"Verification: {action.verify_result}")
            close_notes = "\n".join(b for b in bits if b).strip()

            # POINT 19: reuse the REAL ServiceNow ticket id (the sink created it at
            # diagnosis and wrote it back to the report). This links the precedent to
            # the actual ticket AND makes Writer 1 share the same dedup key (tenant_id,
            # sys_id) as the importer (Writer 2) -> they MERGE instead of duplicating.
            # Fall back to a synthetic id only if no ticket exists (ServiceNow disabled).
            number = report.servicenow_number or f"RCA-{report_id}"
            sys_id = report.servicenow_sys_id or f"serverops-report-{report_id}"
            rec = {
                "number":            number,
                "sys_id":            sys_id,
                "short_description": short_desc,
                "description":       primary.get("observation") or report.summary,
                "close_notes":       close_notes,
                "category":          "Software",
                "cmdb_ci":           system,
                "state":             "Closed",
                "outcome":           outcome,
                "resolved_at":       datetime.now(timezone.utc),
                "correlation_id":    _correlation_id(report),
                "_raw": {"_serverops_feedback": True, "report_id": report_id,
                         "action_id": action_id, "final_state": final_state},
            }
            load_incidents(session, tenant_id, [rec])
            session.flush()
            h = session.scalar(
                select(HistoricalIncident).where(
                    HistoricalIncident.tenant_id == tenant_id,
                    HistoricalIncident.sys_id == rec["sys_id"],
                )
            )
            incident_id = h.incident_id if h else None
            session.commit()

        if incident_id is not None:
            _index_one(incident_id, tenant_id, config)
        log.info("capture_precedent: report=%s outcome=%s -> historical_incident=%s",
                 report_id, outcome, incident_id)
        return incident_id
    except Exception:
        log.exception("capture_precedent: failed (non-fatal); remediation continues")
        return None


def _index_one(incident_id: int, tenant_id: int, config: dict) -> None:
    """Embed the captured incident's problem text and add it to the vector store."""
    try:
        from app.vectorstore import Embedder, VectorRecord, get_vector_store
        store = get_vector_store(config)
        embedder = Embedder(config)
        with SessionLocal() as session:
            h = session.get(HistoricalIncident, incident_id)
            text = problem_text(h) if h else ""
        if not text:
            return
        store.upsert([VectorRecord(
            id=incident_id, vector=embedder.embed_one(text), metadata={"tenant_id": tenant_id}
        )])
    except Exception:
        log.exception("capture_precedent: vector indexing failed (non-fatal)")
