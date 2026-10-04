"""APIFICATION: HTTP client the Streamlit UI uses to talk to core-service.

Replaces every SessionLocal / decide_action / vectorstore call in the UI with
a REST round-trip. The UI container never opens a DB connection.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
import pandas as pd


def _base_url() -> str:
    return os.getenv("CORE_SERVICE_URL", "http://localhost:8000").rstrip("/")


def _client() -> httpx.Client:
    # Fresh client per call — cheap, avoids event-loop shenanigans with
    # Streamlit's fragment/rerun model.
    return httpx.Client(base_url=_base_url(), timeout=float(os.getenv("CORE_HTTP_TIMEOUT", "30")))


# ── /api/v1/query — named-view escape hatch ─────────────────────────────────
# APIFICATION: columns whose values arrive as ISO-8601 strings should be
# re-parsed to pandas Timestamps so the UI can call `.strftime(...)` on them
# exactly as it did when reading straight from SQLAlchemy. Matches every
# datetime column exposed by the view registry.
_DATETIME_COLUMNS = frozenset({
    "timestamp", "created_at", "produced_at", "processed_at",
    "time_min", "time_max", "start_time", "end_time",
    "decided_at", "executed_at", "verified_at",
})


def _decode_datetime_columns(df: pd.DataFrame) -> pd.DataFrame:
    for col in df.columns:
        if col in _DATETIME_COLUMNS:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=False)
    return df


def query_df(view: str, params: dict[str, Any] | None = None) -> pd.DataFrame:
    with _client() as c:
        r = c.post("/api/v1/query", json={"view": view, "params": params or {}})
        r.raise_for_status()
        data = r.json()
    df = pd.DataFrame(data["rows"], columns=data["columns"])
    return _decode_datetime_columns(df)


def query_scalar(view: str, params: dict[str, Any] | None = None) -> Any:
    df = query_df(view, params)
    if df.empty or len(df.columns) == 0:
        return None
    return df.iloc[0, 0]


# ── /api/v1/actions/{id}/decide — the only write from the UI ────────────────
def decide_action(action_id: int, new_state: str, approver_id: str, note: str) -> dict[str, Any]:
    with _client() as c:
        r = c.post(
            f"/api/v1/actions/{action_id}/decide",
            json={"new_state": new_state, "approver_id": approver_id, "note": note},
        )
        # Do not raise_for_status — the UI wants to see error messages too.
        try:
            body = r.json()
        except Exception:
            body = {"ok": False, "snow_posted": False, "message": r.text}
    body.setdefault("ok", False)
    body.setdefault("snow_posted", False)
    body.setdefault("message", "")
    return body


# ── /api/v1/precedent/* — Precedent Memory page ─────────────────────────────
def precedent_status(tenant_id: int) -> dict[str, Any]:
    with _client() as c:
        r = c.get("/api/v1/precedent/status", params={"tenant_id": tenant_id})
        r.raise_for_status()
        return r.json()


def precedent_search(tenant_id: int, query: str, k: int = 5) -> dict[str, Any]:
    with _client() as c:
        r = c.post(
            "/api/v1/precedent/search",
            json={"tenant_id": tenant_id, "query": query, "k": k},
        )
        r.raise_for_status()
        return r.json()


# ── config-service — Source Configuration page ─────────────────────────────
def _config_client() -> httpx.Client:
    base = os.getenv("CONFIG_SERVICE_URL", "http://localhost:8200").rstrip("/")
    return httpx.Client(base_url=base, timeout=float(os.getenv("CONFIG_HTTP_TIMEOUT", "15")))


def save_source_config(tenant_id: int, source_type: str, key: str, value: str) -> dict[str, Any]:
    with _config_client() as c:
        r = c.post(
            "/configs",
            json={"tenant_id": tenant_id, "source_type": source_type, "key": key, "value": value},
        )
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail")
            except Exception:
                detail = r.text
            if isinstance(detail, list):  # SOURCE-CONFIG: pydantic 422 → readable message
                detail = "; ".join(str(d.get("msg", d)) for d in detail)
            raise RuntimeError(str(detail))
        return r.json()


def list_source_configs(tenant_id: int) -> list[dict[str, Any]]:
    with _config_client() as c:
        r = c.get("/configs", params={"tenant_id": tenant_id})
        r.raise_for_status()
        return r.json()


# SOURCE-CONFIG: dropdown catalog comes from config-service (single source of truth).
def list_source_types() -> list[dict[str, str]]:
    with _config_client() as c:
        r = c.get("/source-types")
        r.raise_for_status()
        return r.json()


def delete_source_config(tenant_id: int, source_type: str, key: str) -> dict[str, Any]:
    with _config_client() as c:
        r = c.delete(
            "/configs",
            params={"tenant_id": tenant_id, "source_type": source_type, "key": key},
        )
        r.raise_for_status()
        return r.json()
