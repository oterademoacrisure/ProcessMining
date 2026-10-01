"""config-service — independent API for operator-managed source configuration.

Each entry is (source_type, key, value), e.g. (appian, UI, /path/to/ui).
This service owns validation and the public contract; it has NO database
connection. Saves and lists are forwarded to the aggregator (core-service),
which persists them in the source_config table.

Endpoints:
    GET  /health
    GET  /source-types                — dropdown values for the UI
    POST /configs                     — validate + save (upsert) one entry
    GET  /configs?tenant_id=&source_type=
                                      — list saved entries (readers call this
                                        at the start of each poll cycle)

Run locally:
    uvicorn services.config.main:app --host 0.0.0.0 --port 8200
"""
from __future__ import annotations

import logging
import os
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator


log = logging.getLogger("config-service")

SourceType = Literal["kubernetes", "camunda", "appian"]
SOURCE_TYPES: list[dict[str, str]] = [
    {"value": "kubernetes", "label": "Kubernetes"},
    {"value": "camunda",    "label": "Camunda"},
    {"value": "appian",     "label": "Appian"},
]


def _aggregator() -> httpx.Client:
    base = os.getenv("AGGREGATOR_SERVICE_URL", "http://core-service:8000").rstrip("/")
    return httpx.Client(base_url=base, timeout=float(os.getenv("AGGREGATOR_HTTP_TIMEOUT", "10")))


app = FastAPI(title="serverops-config", version="1.0.0")


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
    value: str

    @field_validator("key", "value")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        return v


def _forward(method: str, path: str, **kwargs: Any) -> Any:
    try:
        with _aggregator() as c:
            r = c.request(method, path, **kwargs)
    except httpx.HTTPError as e:
        log.warning("aggregator unreachable: %s", e)
        raise HTTPException(status_code=502, detail=f"aggregator unreachable: {e}")
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"aggregator error {r.status_code}: {r.text}")
    return r.json()


@app.post("/configs")
def save_config(body: ConfigIn) -> dict[str, Any]:
    return _forward("PUT", "/api/v1/source-configs", json=body.model_dump())


@app.get("/configs")
def list_configs(
    tenant_id: int = Query(1, ge=1),
    source_type: SourceType | None = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"tenant_id": tenant_id}
    if source_type:
        params["source_type"] = source_type
    return _forward("GET", "/api/v1/source-configs", params=params)


if __name__ == "__main__":
    # `python -m services.config.main` — dev only.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8200")))
