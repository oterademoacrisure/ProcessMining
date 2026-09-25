"""APIFICATION: core-service — FastAPI HTTP surface for the UI + background
pipeline loop that polls reader-service and runs dispatch → investigate →
remediate cycles.

Run locally:
    uvicorn services.core.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

# Allow `python -m services.core.main` from the repo root, and container start.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from fastapi import FastAPI

from services.core.api.routes import router as api_router
from services.core.pipeline import Pipeline, build_pipeline


log = logging.getLogger("core-service")

app = FastAPI(title="serverops-core", version="1.0.0")
app.include_router(api_router)


_PIPELINE: Pipeline | None = None


@app.on_event("startup")
def _startup() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    global _PIPELINE
    if os.getenv("DISABLE_PIPELINE", "").lower() in ("1", "true", "yes"):
        log.info("pipeline background loop disabled via DISABLE_PIPELINE")
        return
    _PIPELINE = build_pipeline()
    _PIPELINE.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    global _PIPELINE
    if _PIPELINE is not None:
        _PIPELINE.stop()
        _PIPELINE.reader_client.close()
        _PIPELINE = None


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "service": "core",
        "pipeline_running": bool(_PIPELINE and _PIPELINE._thread and _PIPELINE._thread.is_alive()),
    }


if __name__ == "__main__":
    # `python -m services.core.main` — dev only.
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
