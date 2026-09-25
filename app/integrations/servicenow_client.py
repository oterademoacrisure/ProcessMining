"""Lightweight ServiceNow client used outside the sink pipeline.

The sink (`app/sinks/servicenow_sink.py`) creates / patches tickets at
report-time. This module provides reusable HTTP helpers for actions
triggered by other parts of the system (e.g., the Streamlit approval UI
posting a comment when an operator decides on an action).

Reads SERVICENOW_INSTANCE_URL / SERVICENOW_USERNAME / SERVICENOW_PASSWORD
from environment (loaded via dotenv). Returns False on failure rather than
raising — callers (UI) can degrade gracefully.
"""
from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv


log = logging.getLogger(__name__)


def _config() -> tuple[str, str, str] | None:
    load_dotenv()
    url = (os.getenv("SERVICENOW_INSTANCE_URL", "") or "").rstrip("/")
    user = os.getenv("SERVICENOW_USERNAME", "")
    pwd = os.getenv("SERVICENOW_PASSWORD", "")
    if not (url and user and pwd):
        return None
    return url, user, pwd


def post_comment(sys_id: str, comment: str, timeout: int = 20) -> bool:
    """Append a comment (customer-visible journal entry) to an incident.

    Returns True on success, False on any failure (logged).
    """
    if not sys_id or not comment:
        return False

    cfg = _config()
    if cfg is None:
        log.warning("post_comment: SERVICENOW_* env vars not set; skipping")
        return False
    url, user, pwd = cfg

    api_url = f"{url}/api/now/table/incident/{sys_id}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.patch(
                api_url,
                json={"comments": comment},
                auth=(user, pwd),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                log.error(
                    "post_comment: PATCH %s on %s — body=%s",
                    resp.status_code, sys_id, resp.text[:600],
                )
                return False
        return True
    except Exception:
        log.exception("post_comment: PATCH failed for sys_id=%s", sys_id)
        return False


def resolve_incident(
    sys_id: str,
    close_notes: str,
    close_code: str = "Solved (Permanently)",
    timeout: int = 20,
) -> bool:
    """POINT 19 (Step 7): set an incident to Resolved with a close code + notes,
    closing the ITSM loop after a verified remediation.

    `close_code` starting with "Solved" -> the history sync derives outcome=approved
    when it pulls the resolved ticket back (see loader.derive_outcome). Returns True
    on success, False on any failure (logged). No-ops if ServiceNow isn't configured.
    """
    if not sys_id:
        return False
    cfg = _config()
    if cfg is None:
        log.warning("resolve_incident: SERVICENOW_* env vars not set; skipping")
        return False
    url, user, pwd = cfg

    api_url = f"{url}/api/now/table/incident/{sys_id}"
    payload = {
        "state": "6",   # 6 = Resolved
        "close_code": close_code,
        "close_notes": close_notes or "Resolved by serverops automated remediation (verified).",
    }
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.patch(
                api_url,
                json=payload,
                auth=(user, pwd),
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if resp.status_code >= 400:
                log.error(
                    "resolve_incident: PATCH %s on %s — body=%s",
                    resp.status_code, sys_id, resp.text[:600],
                )
                return False
        log.info("resolve_incident: incident %s set to Resolved (%s)", sys_id, close_code)
        return True
    except Exception:
        log.exception("resolve_incident: PATCH failed for sys_id=%s", sys_id)
        return False