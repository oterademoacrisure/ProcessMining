"""config-service — independent API for operator-managed source configuration.

Each entry is (source_type, key, value):
    source_type — one kind of telemetry (routes to a reader + analyzer in
                  config/modules.yaml), e.g. kubernetes_pod_logs
    key         — the component / instance name, e.g. vote; readers stamp it
                  as system_id on every event
    value       — how to reach it, e.g. http://host/logs?app=vote&tail=200

This service owns the source-type catalog, validation and the public
contract; it has NO database connection. Saves, lists and deletes are
forwarded to the aggregator (core-service), which persists them in the
source_config table.

Endpoints:
    GET    /health
    GET    /source-types              — catalog for the UI dropdown (+ input hints)
    POST   /configs                   — validate + save (upsert) one entry
    GET    /configs?tenant_id=&source_type=
                                      — list saved entries (readers call this
                                        at the start of each poll cycle)
    DELETE /configs?tenant_id=&source_type=&key=
                                      — remove one entry

Run locally:
    uvicorn services.config.main:app --host 0.0.0.0 --port 8200
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator


log = logging.getLogger("config-service")

# SOURCE-CONFIG: the one catalog of source types. The UI builds its dropdown
# from GET /source-types, so add new types here only. Naming follows the
# modules.yaml convention: <platform>_<telemetry kind>.
#   value_kind "url"  — value must be an http(s) URL the reader fetches
#   value_kind "text" — free text (e.g. a path)
SourceType = Literal["kubernetes_pod_logs", "kubernetes_events", "prometheus", "appian",
                     "azure_container_logs"]  # AZURE-MONITOR
SOURCE_TYPES: list[dict[str, str]] = [
    {"value": "kubernetes_pod_logs", "label": "Kubernetes Pod Logs", "value_kind": "url",
     "key_hint": "e.g. vote", "value_hint": "e.g. http://host/logs?app=vote&tail=200"},
    {"value": "kubernetes_events",   "label": "Kubernetes Events",   "value_kind": "url",
     "key_hint": "e.g. default", "value_hint": "e.g. http://host/events?namespace=default"},
    {"value": "prometheus",          "label": "Prometheus",          "value_kind": "url",
     "key_hint": "namespace, e.g. default",  # AZURE-MONITOR
     "value_hint": "e.g. https://<name>.<region>.prometheus.monitor.azure.com"},
    {"value": "azure_container_logs", "label": "Azure Container Logs", "value_kind": "url",  # AZURE-MONITOR
     "key_hint": "namespace, e.g. default",
     "value_hint": "e.g. https://api.loganalytics.io/v1/workspaces/<WORKSPACE-ID>/query"},
    {"value": "appian",              "label": "Appian",              "value_kind": "text",
     "key_hint": "e.g. UI", "value_hint": "e.g. /opt/appian/ui"},
]
_VALUE_KIND = {t["value"]: t["value_kind"] for t in SOURCE_TYPES}

# SOURCE-CONFIG: key becomes system_id downstream — keep it a plain identifier.
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _aggregator() -> httpx.Client:
    base = os.getenv("AGGREGATOR_SERVICE_URL", "http://localhost:8000").rstrip("/")
    return httpx.Client(base_url=base, timeout=float(os.getenv("AGGREGATOR_HTTP_TIMEOUT", "10")))


app = FastAPI(title="serverops-config", version="1.1.0")


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "service": "config"}


@app.get("/source-types")
def source_types() -> list[dict[str, str]]:
    return SOURCE_TYPES


class ConfigIn(BaseModel):
    tenant_id: int = Field(1, ge=1)
    source_type: SourceType
    key: str = Field(..., max_length=128)
    value: str = Field(..., max_length=2048)

    @field_validator("key", "value")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v

    @field_validator("key")
    @classmethod
    def _key_shape(cls, v: str) -> str:
        if not _KEY_RE.match(v):
            raise ValueError("use letters, digits, '-', '_' or '.' (e.g. vote, redis-01)")
        return v

    # SOURCE-CONFIG: URL-based sources are fetched by the reader, so reject
    # anything it could not (or must not) call.
    @model_validator(mode="after")
    def _value_shape(self) -> "ConfigIn":
        if _VALUE_KIND[self.source_type] != "url":
            return self
        parts = urlsplit(self.value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("value must be an http(s) URL, e.g. http://host/logs?app=vote")
        if parts.username or parts.password:
            raise ValueError("do not put credentials in the URL — store them in Key Vault")
        return self


def _forward(method: str, path: str, **kwargs: Any) -> Any:
    try:
        with _aggregator() as c:
            r = c.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        log.warning("aggregator unreachable: %s", e)
        raise HTTPException(status_code=502, detail=f"aggregator unreachable: {e}")
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="not found")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"aggregator error {r.status_code}: {r.text}")
    return r.json()


@app.post("/configs")
def save_config(body: ConfigIn) -> dict[str, Any]:
    return _forward("PUT", "/api/v1/source-configs", json=body.model_dump())


# SOURCE-CONFIG: list/delete take source_type as a plain string (not the
# catalog Literal) so rows saved under retired names can still be found and
# cleaned up.
@app.get("/configs")
def list_configs(
    tenant_id: int = Query(1, ge=1),
    source_type: str | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if source_type:
        params["source_type"] = source_type
    return _forward("GET", "/api/v1/source-configs", params=params)


@app.delete("/configs")
def delete_config(
    source_type: str,
    key: str,
    tenant_id: int = Query(1, ge=1),
) -> dict[str, Any]:
    return _forward(
        "DELETE", "/api/v1/source-configs",
        params={"tenant_id": tenant_id, "source_type": source_type, "key": key},
    )


if __name__ == "__main__":
    # `python -m services.config.main` — dev only.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8200")))
