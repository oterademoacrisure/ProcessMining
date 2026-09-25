"""POINT 19 (Task #19): ServiceNow REST client for pulling resolved incidents.

This module ONLY fetches from ServiceNow and returns plain Python dicts. It does
NOT touch our database (that is loader.py's job). Keeping "fetch" and "store"
separate means each can be tested/replaced on its own (separation of concerns).

Concepts used here:
  - sysparm_query        : ServiceNow's filter language (like a SQL WHERE)
  - sysparm_display_value: ask for human-readable names, not internal ids
                           (reference resolution, e.g. cmdb_ci -> "credit-bureau-prod")
  - pagination           : pull a long list in fixed-size pages (offset + limit)
  - incremental sync     : optional `since` timestamp -> only changed rows
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv


log = logging.getLogger(__name__)


# POINT 19: ServiceNow incident states we treat as "history worth learning from".
#   6 = Resolved, 7 = Closed. (Open/in-progress have no resolution yet.)
RESOLVED_STATES = ("6", "7")

# POINT 19: the fields we pull. Kept small for fast responses; raw payload is
# preserved separately (see _normalize -> "_raw").
_FIELDS = (
    "number,sys_id,short_description,description,close_notes,close_code,category,"
    "subcategory,cmdb_ci,priority,state,opened_at,resolved_at,closed_at,"
    "correlation_id,sys_updated_on"
)


class ServiceNowHistoryClient:
    """Reads resolved incidents from the ServiceNow Table API."""

    def __init__(self, config: dict | None = None):
        load_dotenv()
        config = config or {}
        # POINT 19: same credentials the write-side sink uses (read access only
        # is required here).
        self.instance_url = (
            config.get("instance_url") or os.getenv("SERVICENOW_INSTANCE_URL", "")
        ).rstrip("/")
        self.username = config.get("username") or os.getenv("SERVICENOW_USERNAME", "")
        self.password = config.get("password") or os.getenv("SERVICENOW_PASSWORD", "")
        self.table = config.get("servicenow_table", "incident")
        self.timeout = int(config.get("http_timeout_sec", 30))
        self.page_size = int(config.get("page_size", 100))
        self.max_retries = int(config.get("max_retries", 2))

    def is_configured(self) -> bool:
        return bool(self.instance_url and self.username and self.password)

    # ── public API ──────────────────────────────────────────────────────────

    def fetch_resolved_incidents(
        self,
        since: datetime | None = None,
        max_records: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return resolved/closed incidents that have a resolution note.

        Args:
          since: if given, only incidents updated at/after this time (UTC) are
                 pulled (incremental sync). If None, pulls everything (backfill).
          max_records: optional safety cap on total rows.
        """
        if not self.is_configured():
            raise RuntimeError(
                "ServiceNowHistoryClient: SERVICENOW_INSTANCE_URL / USERNAME / "
                "PASSWORD are not all set in .env"
            )

        query = self._build_query(since)
        api_url = f"{self.instance_url}/api/now/table/{self.table}"

        results: list[dict[str, Any]] = []
        offset = 0
        # POINT 19: pagination loop — keep pulling pages until a short page
        # signals the end (or we hit the cap).
        while True:
            page = self._get_page(api_url, query, offset)
            if not page:
                break
            results.extend(self._normalize(r) for r in page)
            if max_records and len(results) >= max_records:
                results = results[:max_records]
                break
            if len(page) < self.page_size:
                break  # last page
            offset += self.page_size

        log.info("ServiceNowHistoryClient: fetched %d resolved incident(s)", len(results))
        return results

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_query(self, since: datetime | None) -> str:
        # POINT 19: filter = resolved/closed AND has a resolution note. Ordered
        # by last-updated so incremental pulls are predictable. sysparm_query
        # always matches on RAW values (codes), regardless of display_value.
        parts = [f"stateIN{','.join(RESOLVED_STATES)}", "close_notesISNOTEMPTY"]
        if since is not None:
            # ServiceNow expects "YYYY-MM-DD HH:MM:SS" (UTC) for date comparisons.
            parts.append(f"sys_updated_on>={since.strftime('%Y-%m-%d %H:%M:%S')}")
        return "^".join(parts) + "^ORDERBYsys_updated_on"

    def _get_page(self, api_url: str, query: str, offset: int) -> list[dict]:
        params = {
            "sysparm_query": query,
            "sysparm_fields": _FIELDS,
            # POINT 19: "all" returns BOTH the raw value and the display label
            # for every field, so we can take readable names (cmdb_ci) AND raw
            # datetimes — see _normalize.
            "sysparm_display_value": "all",
            "sysparm_limit": str(self.page_size),
            "sysparm_offset": str(offset),
        }
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(
                        api_url, params=params, auth=(self.username, self.password),
                        headers={"Accept": "application/json"},
                    )
                if resp.status_code == 401:
                    raise RuntimeError("ServiceNow returned 401 — credentials rejected")
                if resp.status_code >= 500:
                    raise httpx.HTTPError(f"ServiceNow {resp.status_code}")  # retry path
                resp.raise_for_status()
                return resp.json().get("result", []) or []
            except (httpx.HTTPError, httpx.ConnectError) as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(1.5 * (attempt + 1))  # simple backoff
                    continue
                log.error("ServiceNowHistoryClient: GET failed at offset=%d: %s", offset, e)
                raise
        if last_err:
            raise last_err
        return []

    @staticmethod
    def _normalize(raw: dict) -> dict[str, Any]:
        """Flatten one ServiceNow record (display_value='all' format) into the
        shape the loader stores. With display_value='all', every field is an
        object {"value": <raw>, "display_value": <label>}."""

        def disp(field: str) -> str | None:
            v = raw.get(field)
            if isinstance(v, dict):
                v = v.get("display_value")
            return v or None

        def val(field: str) -> str | None:
            v = raw.get(field)
            if isinstance(v, dict):
                v = v.get("value")
            return v or None

        return {
            "number":            val("number"),
            "sys_id":            val("sys_id"),
            "short_description": val("short_description"),
            "description":       val("description"),
            "close_notes":       val("close_notes"),
            "close_code":        disp("close_code") or val("close_code"),  # POINT 19: -> outcome
            "category":          disp("category"),
            "subcategory":       disp("subcategory"),
            "cmdb_ci":           disp("cmdb_ci"),     # POINT 19: readable name, not the id
            "priority":          disp("priority"),
            "state":             disp("state"),
            # POINT 19: take RAW datetimes (consistent "YYYY-MM-DD HH:MM:SS" UTC),
            # not display values (which vary by instance locale).
            "opened_at":         val("opened_at"),
            "resolved_at":       val("resolved_at") or val("closed_at"),
            "correlation_id":    val("correlation_id"),
            "sys_updated_on":    val("sys_updated_on"),   # POINT 19: source update time -> incremental watermark
            "_raw":              raw,                  # POINT 19: full payload for raw_json
        }
