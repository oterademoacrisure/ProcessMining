"""APIFICATION: reader-service — FastAPI wrapper around app/readers/.

Endpoints:
    GET  /health                             — liveness
    GET  /sources                            — configured source_types
    POST /sources/{source_type}/poll         — read one batch, keep pending
    POST /sources/{source_type}/ack          — commit reader state

The service is polled by core-service. It does NOT touch Postgres.

Run locally:
    uvicorn services.reader.main:app --host 0.0.0.0 --port 8100
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

# Allow `python -m services.reader.main` from the repo root, and container start.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from app.orchestrator.registry import Registry
from app.paths import PROJECT_ROOT
from services.reader.batch_store import STORE, payload_to_dict


log = logging.getLogger("reader-service")

_CONFIG_PATH = Path(os.getenv("MODULES_YAML", PROJECT_ROOT / "config" / "modules.yaml"))
_REGISTRY: Registry | None = None


def _registry() -> Registry:
    """Lazy-load the Registry once. Reader-service only uses source_types()
    and build_reader(); the rest of the YAML (investigators/remediation) is
    parsed but unused here — harmless."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = Registry.load(_CONFIG_PATH)
    return _REGISTRY


app = FastAPI(title="serverops-reader", version="1.0.0")


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Warm the registry so a bad YAML fails fast on startup, not on first poll.
    r = _registry()
    log.info("reader-service ready — sources=%s config=%s", r.source_types(), _CONFIG_PATH)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "reader",
        "pending_batches": STORE.pending_count(),
        "sources": _registry().source_types(),
    }


@app.get("/sources")
def list_sources() -> dict[str, list[str]]:
    return {"sources": _registry().source_types()}


class PollResponse(BaseModel):
    source_type: str
    tenant_id: int
    batch_id: str | None
    count: int
    events: list[dict[str, Any]]


@app.post("/sources/{source_type}/poll", response_model=PollResponse)
def poll(source_type: str, tenant_id: int = Query(1, ge=1)) -> PollResponse:
    reg = _registry()
    if source_type not in reg.source_types():
        raise HTTPException(status_code=404, detail=f"unknown source_type: {source_type}")

    try:
        reader = reg.build_reader(source_type, tenant_id=tenant_id)
    except Exception as e:
        log.exception("failed to build reader for source_type=%s", source_type)
        raise HTTPException(status_code=500, detail=f"reader build failed: {e}")

    try:
        payloads = list(reader.read())
    except Exception as e:
        log.exception("reader.read() failed for source_type=%s", source_type)
        raise HTTPException(status_code=500, detail=f"reader.read failed: {e}")

    if not payloads:
        return PollResponse(
            source_type=source_type,
            tenant_id=tenant_id,
            batch_id=None,
            count=0,
            events=[],
        )

    batch_id = STORE.register(source_type, tenant_id, reader, payloads)
    return PollResponse(
        source_type=source_type,
        tenant_id=tenant_id,
        batch_id=batch_id,
        count=len(payloads),
        events=[payload_to_dict(p) for p in payloads],
    )


class AckRequest(BaseModel):
    batch_id: str


class AckResponse(BaseModel):
    ok: bool
    committed: bool
    detail: str | None = None


@app.post("/sources/{source_type}/ack", response_model=AckResponse)
def ack(source_type: str, body: AckRequest) -> AckResponse:
    # source_type is in the path for symmetry with /poll and future per-source
    # metrics; the batch_id itself is globally unique so it's sufficient.
    try:
        committed = STORE.ack(body.batch_id)
    except Exception as e:
        log.exception("reader.commit failed for batch_id=%s", body.batch_id)
        raise HTTPException(status_code=500, detail=f"commit failed: {e}")
    if not committed:
        return AckResponse(ok=True, committed=False, detail="unknown or already-acked batch_id")
    return AckResponse(ok=True, committed=True)


if __name__ == "__main__":
    # `python -m services.reader.main` — dev only.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8100")))
