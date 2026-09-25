"""ServiceNow ticket creation sink for RootCauseReport.

Auto-creates / updates ServiceNow incidents from investigator-produced
RootCauseReports.

Behavior (ticket-state-aware, Option 3):
  - correlation_id is stable per INCIDENT IDENTITY (subject + source +
    severity), NOT per finding. All recurrences of the same problem
    share the same correlation_id.
  - On each report:
      1. GET by correlation_id (any state).
      2. If a matching ticket exists AND its state is in `open_states`
         (default: New / In Progress / On Hold) -> PATCH a `comments`
         entry on it. No new ticket.
      3. If ticket is in a closed state (Resolved / Closed / Canceled)
         OR none exists -> POST create a new ticket. This handles "real"
         recurrences (where the prior incident was deemed fixed).

Modes (config.servicenow_mode or auto-detected):
  - real:     POST/PATCH to ServiceNow REST API. Requires env vars.
  - mock:     Write JSON files to data/output/servicenow_mock/. Defaults
              to this when credentials are missing.
  - dry_run:  Log payload to stdout, no side effects.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv
from sqlalchemy import update

from app.db.models import RootCauseReport as RootCauseReportRow
from app.db.session import SessionLocal
from app.findings import RootCauseReport
from app.paths import resolve_under_project
from app.sinks.report_sinks import BaseReportSink
from app.observability import progress  # POINT 21 (Task #21): live stage events


log = logging.getLogger(__name__)


# ServiceNow uses 1 = High, 2 = Medium, 3 = Low for both urgency and impact.
SEVERITY_TO_PRIORITY = {
    "critical": ("1", "1"),
    "high":     ("2", "2"),
    "normal":   ("3", "3"),
}

# Default ServiceNow incident state values that count as "still open" — i.e.
# the operator hasn't resolved the incident, so a recurrence should comment
# on the existing ticket rather than create a new one. Tunable in YAML.
#   1 = New, 2 = In Progress, 3 = On Hold
#   6 = Resolved, 7 = Closed, 8 = Canceled
DEFAULT_OPEN_STATES = ("1", "2", "3")


class ServiceNowReportSink(BaseReportSink):
    """Creates or updates a ServiceNow incident from each RootCauseReport."""

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        load_dotenv()

        self.instance_url = (
            self.config.get("instance_url")
            or os.getenv("SERVICENOW_INSTANCE_URL", "")
        ).rstrip("/")
        self.username = self.config.get("username") or os.getenv("SERVICENOW_USERNAME", "")
        self.password = self.config.get("password") or os.getenv("SERVICENOW_PASSWORD", "")
        self.table = self.config.get("servicenow_table", "incident")
        self.assignment_group = self.config.get("assignment_group")
        self.category = self.config.get("category", "Software")
        self.subcategory = self.config.get("subcategory", "Application")
        self.short_description_max = int(self.config.get("short_description_max", 160))
        self.http_timeout_sec = int(self.config.get("http_timeout_sec", 30))
        self.low_confidence_threshold = float(self.config.get("low_confidence_threshold", 0.7))

        open_states_cfg = self.config.get("open_states")
        if isinstance(open_states_cfg, (list, tuple)) and open_states_cfg:
            self.open_states = tuple(str(s) for s in open_states_cfg)
        else:
            self.open_states = DEFAULT_OPEN_STATES

        explicit_mode = self.config.get("servicenow_mode")
        if explicit_mode in ("real", "mock", "dry_run"):
            self.mode = explicit_mode
        elif self.instance_url and self.username and self.password:
            self.mode = "real"
        else:
            self.mode = "mock"
            log.warning(
                "ServiceNowReportSink: SERVICENOW_INSTANCE_URL / USERNAME / PASSWORD "
                "not all set — defaulting to 'mock' mode (writes JSON files locally)"
            )

        self.mock_dir = resolve_under_project(
            self.config.get("mock_dir", "data/output/servicenow_mock")
        )
        if self.mode == "mock":
            self.mock_dir.mkdir(parents=True, exist_ok=True)

    # ─────────────────────────────────────────────────────────────────────

    def handle(self, report: RootCauseReport) -> None:
        correlation_id = self._correlation_id(report)
        ticket = self._build_ticket(report, correlation_id)

        # POINT 21 (Task #21): live timeline — SNOW_CREATE stage. Thread onto the SAME
        # key the investigator saved (timeline_correlation_id). The sink's own
        # correlation_id uses the LLM-mapped REPORT severity, while DIAGNOSE used the
        # FINDING severity — if the LLM upgraded severity those differ, which would
        # orphan SNOW_CREATE onto its own thread. Fall back to the sink key if absent.
        _tenant = (report.correlation_keys or {}).get("tenant_id")
        _tl_corr = (report.payload or {}).get("timeline_correlation_id") or correlation_id
        progress.emit("SNOW_CREATE", "started", tenant_id=_tenant, correlation_id=_tl_corr,
                      step="Raising an incident ticket in ServiceNow")

        if self.mode == "dry_run":
            log.info("ServiceNowReportSink[dry_run] would create/update: correlation_id=%s", correlation_id)
            print(f"[ServiceNow DRY RUN] correlation_id={correlation_id}")
            print(json.dumps(ticket, indent=2, default=str))
        elif self.mode == "mock":
            self._write_mock(correlation_id, ticket, report)
        else:
            self._post_to_servicenow(correlation_id, ticket, report)

        progress.emit("SNOW_CREATE", "succeeded", tenant_id=_tenant, correlation_id=_tl_corr,
                      step="Incident ticket raised in ServiceNow",
                      meta={"inc": report.servicenow_number, "mode": self.mode})

    # ─────────────────────────────────────────────────────────────────────
    # Ticket building
    # ─────────────────────────────────────────────────────────────────────

    def _correlation_id(self, report: RootCauseReport) -> str:
        """Stable per incident IDENTITY (subject + source + severity), not
        per finding. Re-occurrences of the same problem map to the same
        correlation_id so the sink can find the existing ticket and
        comment on it rather than create a duplicate.
        """
        ck = report.correlation_keys or {}
        tenant = ck.get("tenant_id", "x")
        payload = report.payload or {}
        primary = payload.get("primary_trigger") or {}
        source = _slug(primary.get("source_type") or "unknown")
        subject = _slug(primary.get("subject") or "unknown")
        severity = _slug(report.severity or "unknown")
        return f"rca-t{tenant}-{source}-{subject}-{severity}"[:100]

    def _build_ticket(self, report: RootCauseReport, correlation_id: str) -> dict[str, Any]:
        ck = report.correlation_keys or {}
        payload = report.payload or {}
        llm_raw = payload.get("llm_raw") if isinstance(payload, dict) else None

        urgency, impact = SEVERITY_TO_PRIORITY.get(report.severity, ("3", "3"))
        primary_host = (ck.get("infra_hosts") or [None])[0]

        short_desc = (report.summary or report.investigator_class or "RCA incident").strip()

        # Surface low-confidence reports in the title so operators see the
        # caveat without opening the description.
        confidence = _extract_confidence(report)
        if confidence is not None and confidence < self.low_confidence_threshold:
            prefix = f"[LOW CONFIDENCE {confidence:.2f}] "
            short_desc = prefix + short_desc

        if len(short_desc) > self.short_description_max:
            short_desc = short_desc[: self.short_description_max - 1] + "…"

        description = self._format_description(report, llm_raw, ck)

        ticket: dict[str, Any] = {
            "short_description":  short_desc,
            "description":        description,
            "urgency":            urgency,
            "impact":             impact,
            "category":           self.category,
            "subcategory":        self.subcategory,
            "correlation_id":     correlation_id,
            "correlation_display": f"rca:{report.investigator_class}",
        }
        if primary_host:
            ticket["cmdb_ci"] = primary_host
        if self.assignment_group:
            ticket["assignment_group"] = self.assignment_group
        return ticket

    def _build_recurrence_comment(self, report: RootCauseReport) -> str:
        """Compact comment posted on an existing open ticket when the same
        incident recurs. Activity-log appropriate — focused on what's new."""
        ck = report.correlation_keys or {}
        payload = report.payload or {}
        primary = payload.get("primary_trigger") or {}

        now_iso = datetime.now().isoformat(timespec="seconds")
        lines = [
            f"=== RECURRENCE detected at {now_iso} ===",
            "",
            f"Updated summary: {report.summary}",
            f"Severity:        {report.severity}",
            f"Primary trigger: {primary.get('source_type', '?')} / {primary.get('subject', '?')}",
            f"Trigger findings (this cycle): {report.trigger_finding_ids}",
            f"Affected hosts:  {', '.join(ck.get('infra_hosts') or []) or '(none)'}",
            f"Time window:     {ck.get('window', ['?', '?'])}",
        ]

        if isinstance(llm_raw := (payload.get("llm_raw") if isinstance(payload, dict) else None), dict):
            actions = llm_raw.get("recommended_actions") or []
            if actions:
                lines.append("")
                lines.append("Recommended actions (this cycle):")
                for a in actions[:5]:
                    lines.append(f"  - {a}")

        return "\n".join(lines)

    def _format_description(self, report: RootCauseReport, llm_raw, ck: dict) -> str:
        parts: list[str] = [
            "AUTO-GENERATED INCIDENT from RCA pipeline",
            "",
            f"Investigator: {report.investigator_class}",
            f"Severity:     {report.severity}",
            f"Primary trigger finding: #{report.trigger_finding_id}",
            f"All trigger findings:    {report.trigger_finding_ids}",
            "",
            "=== SUMMARY ===",
            report.summary or "(no summary)",
        ]

        if report.evidence_chain:
            parts.append("")
            parts.append("=== EVIDENCE CHAIN ===")
            for i, e in enumerate(report.evidence_chain, 1):
                signal = e.get("signal") or e.get("subject") or e.get("observation") or "(no signal)"
                source = e.get("source") or e.get("source_type") or "?"
                role   = e.get("role")   or e.get("tier")        or "?"
                ts     = e.get("timestamp") or ""
                parts.append(f"  {i}. [{role}] {source} — {signal}  ({ts})")

        if isinstance(llm_raw, dict):
            actions = llm_raw.get("recommended_actions") or []
            if actions:
                parts.append("")
                parts.append("=== RECOMMENDED ACTIONS ===")
                for a in actions:
                    parts.append(f"  - {a}")
            factors = llm_raw.get("contributing_factors") or []
            if factors:
                parts.append("")
                parts.append("=== CONTRIBUTING FACTORS ===")
                for f in factors:
                    parts.append(f"  - {f}")
            uncertainties = llm_raw.get("uncertainties") or []
            if uncertainties:
                parts.append("")
                parts.append("=== UNCERTAINTIES (per the analyst) ===")
                for u in uncertainties:
                    parts.append(f"  - {u}")
            confidence = llm_raw.get("confidence")
            if confidence is not None:
                parts.append("")
                parts.append(f"LLM confidence: {confidence}")

        parts.append("")
        parts.append("=== CORRELATION KEYS ===")
        parts.append(f"Tenant:           {ck.get('tenant_id', '?')}")
        parts.append(f"Affected hosts:   {', '.join(ck.get('infra_hosts') or []) or '(none)'}")
        parts.append(f"Affected cases:   {len(ck.get('case_ids') or [])}")
        parts.append(f"Time window:      {ck.get('window', ['?', '?'])}")
        parts.append(f"Incident size:    {ck.get('incident_size', 1)} finding(s)")

        return "\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────
    # Mock mode (no ServiceNow needed)
    # ─────────────────────────────────────────────────────────────────────

    def _write_mock(self, correlation_id: str, ticket: dict, report: RootCauseReport) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.mock_dir / f"{stamp}-{correlation_id}.json"
        path.write_text(
            json.dumps(
                {
                    "_mock_created_at": datetime.now().isoformat(),
                    "_mock_correlation_id": correlation_id,
                    "_mock_recurrence_comment": self._build_recurrence_comment(report),
                    **ticket,
                },
                indent=2, default=str,
            ),
            encoding="utf-8",
        )
        log.info(
            "ServiceNowReportSink[mock]: wrote %s (correlation_id=%s, urgency=%s)",
            path.name, correlation_id, ticket.get("urgency"),
        )
        # Surface a synthetic "ticket number" + file:// url for mock runs so
        # the UI's back-link still works during demos that don't have a real
        # ServiceNow instance configured.
        self._write_back_to_report(
            report,
            number=f"MOCK-{correlation_id}",
            sys_id=correlation_id,
        )

    # mock mode overrides _ticket_url so the link goes to the local JSON file
    # (only used when self.mode == 'mock').
    def _ticket_url_mock(self, correlation_id: str) -> str:
        return f"file:///{(self.mock_dir / correlation_id).as_posix()}.json"

    # ─────────────────────────────────────────────────────────────────────
    # Real mode: GET-then-create-or-comment
    # ─────────────────────────────────────────────────────────────────────

    def _post_to_servicenow(self, correlation_id: str, ticket: dict, report: RootCauseReport) -> None:
        api_url = f"{self.instance_url}/api/now/table/{self.table}"
        auth = (self.username, self.password)
        headers = {"Accept": "application/json", "Content-Type": "application/json"}

        existing = self._fetch_existing(api_url, auth, correlation_id)
        open_ticket = self._pick_open(existing)

        if open_ticket is not None:
            self._patch_comment(api_url, auth, headers, open_ticket, report, correlation_id)
            # Back-link the existing ticket to THIS report row too, so the UI
            # can show the link even on recurrence reports.
            self._write_back_to_report(
                report,
                number=open_ticket.get("number"),
                sys_id=open_ticket.get("sys_id"),
            )
            return

        # No open ticket -> create new. If a CLOSED ticket exists, this still
        # creates a new one (the prior incident was deemed resolved, so this
        # is a real recurrence worth a fresh ticket).
        self._post_create(api_url, auth, headers, ticket, correlation_id, report, had_closed=bool(existing))

    def _fetch_existing(self, api_url: str, auth, correlation_id: str) -> list[dict]:
        try:
            with httpx.Client(timeout=self.http_timeout_sec) as client:
                resp = client.get(
                    api_url,
                    params={
                        "sysparm_query":  f"correlation_id={correlation_id}^ORDERBYDESCsys_updated_on",
                        "sysparm_limit":  "10",
                        "sysparm_fields": "sys_id,number,state,sys_updated_on",
                    },
                    auth=auth,
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                return resp.json().get("result", []) or []
        except Exception:
            log.exception(
                "ServiceNowReportSink[real]: GET-by-correlation_id failed for %s — will attempt create",
                correlation_id,
            )
            return []

    def _pick_open(self, candidates: list[dict]) -> dict | None:
        for row in candidates:
            if str(row.get("state", "")) in self.open_states:
                return row
        return None

    def _patch_comment(
        self,
        api_url: str,
        auth,
        headers: dict,
        existing_ticket: dict,
        report: RootCauseReport,
        correlation_id: str,
    ) -> None:
        sys_id = existing_ticket.get("sys_id")
        number = existing_ticket.get("number")
        state = existing_ticket.get("state")
        comment = self._build_recurrence_comment(report)

        try:
            with httpx.Client(timeout=self.http_timeout_sec) as client:
                resp = client.patch(
                    f"{api_url}/{sys_id}",
                    json={"comments": comment},
                    auth=auth,
                    headers=headers,
                )
                if resp.status_code >= 400:
                    log.error(
                        "ServiceNowReportSink[real]: PATCH %s for %s — body=%s",
                        resp.status_code, number, resp.text[:1500],
                    )
                    return
            log.info(
                "ServiceNowReportSink[real]: posted recurrence comment on %s (sys_id=%s, state=%s, correlation_id=%s)",
                number, sys_id, state, correlation_id,
            )
        except Exception:
            log.exception(
                "ServiceNowReportSink[real]: PATCH (comment) failed for %s correlation_id=%s",
                number, correlation_id,
            )

    def _post_create(
        self,
        api_url: str,
        auth,
        headers: dict,
        ticket: dict,
        correlation_id: str,
        report: RootCauseReport,
        had_closed: bool,
    ) -> None:
        try:
            with httpx.Client(timeout=self.http_timeout_sec) as client:
                resp = client.post(api_url, json=ticket, auth=auth, headers=headers)
                if resp.status_code >= 400:
                    log.error(
                        "ServiceNowReportSink[real]: POST %s for correlation_id=%s — body=%s",
                        resp.status_code, correlation_id, resp.text[:1500],
                    )
                    return
                result = resp.json().get("result", {})
            label = "fresh recurrence" if had_closed else "new"
            number = result.get("number")
            sys_id = result.get("sys_id")
            log.info(
                "ServiceNowReportSink[real]: created %s (%s) sys_id=%s correlation_id=%s",
                number, label, sys_id, correlation_id,
            )
            self._write_back_to_report(report, number=number, sys_id=sys_id)
        except Exception:
            log.exception(
                "ServiceNowReportSink[real]: POST failed for correlation_id=%s", correlation_id,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Back-link: update root_cause_report row with the SNOW ticket info
    # ─────────────────────────────────────────────────────────────────────

    def _ticket_url(self, sys_id: str) -> str:
        if not sys_id or not self.instance_url:
            return ""
        return f"{self.instance_url}/nav_to.do?uri=incident.do?sys_id={sys_id}"

    def _write_back_to_report(self, report: RootCauseReport, number: str | None, sys_id: str | None) -> None:
        """Persist the SNOW ticket info onto the root_cause_report row that
        was just inserted by the emitter sink. Mutates `report` so any
        later sinks see the back-link too."""
        if not number and not sys_id:
            return
        # POINT 19: servicenow_number / servicenow_sys_id are varchar(64). In mock
        # mode the back-link uses the correlation_id (up to 100 chars) as the sys_id,
        # which overflows the column. Cap both to 64 so --keep-servicenow runs cleanly.
        # (No-op for real ServiceNow ids, which are short.)
        number = number[:64] if number else number
        sys_id = sys_id[:64] if sys_id else sys_id
        url = self._ticket_url(sys_id) if sys_id else None

        # Update the in-memory dataclass first (cheap; gives later sinks
        # visibility) regardless of DB result.
        report.servicenow_number = number
        report.servicenow_sys_id = sys_id
        report.servicenow_url = url

        if not report.report_id:
            log.warning(
                "ServiceNowReportSink: report has no report_id (emitter sink didn't run "
                "before this one?) — skipping DB back-link update for ticket %s",
                number,
            )
            return

        try:
            with SessionLocal() as session:
                session.execute(
                    update(RootCauseReportRow)
                    .where(RootCauseReportRow.report_id == report.report_id)
                    .values(
                        servicenow_number=number,
                        servicenow_sys_id=sys_id,
                        servicenow_url=url,
                    )
                )
                session.commit()
            log.info(
                "ServiceNowReportSink: linked report_id=%d -> %s (sys_id=%s)",
                report.report_id, number, sys_id,
            )
        except Exception:
            log.exception(
                "ServiceNowReportSink: failed to back-link ticket info onto report_id=%d",
                report.report_id,
            )


def _slug(value: Any) -> str:
    """Lowercase, replace non-alphanumeric with hyphens, collapse repeats,
    trim. Keeps correlation_id stable, safe for ServiceNow query strings,
    and bounded in length."""
    s = str(value or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60] if s else "unknown"


def _extract_confidence(report: RootCauseReport) -> float | None:
    """Pull the LLM's self-reported confidence (0.0–1.0) from the payload,
    or None if not present / not numeric."""
    payload = report.payload or {}
    llm_raw = payload.get("llm_raw") if isinstance(payload, dict) else None
    if not isinstance(llm_raw, dict):
        return None
    raw = llm_raw.get("confidence")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
