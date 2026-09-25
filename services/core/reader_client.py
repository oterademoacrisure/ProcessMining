"""APIFICATION: HTTP client for reader-service.

Polls the reader for events, reconstructs EventLogPayload objects, and acks
after successful persistence. Mirrors the semantics of the in-process code
that used to live in app/runner.py :: _ingest_cycle().
"""
from __future__ import annotations

import logging
import os
from dataclasses import fields
from datetime import datetime
from typing import Any

import httpx

from app.readers.base import EventLogPayload


log = logging.getLogger("core.reader_client")


class ReaderClient:
    def __init__(self, base_url: str | None = None, timeout: float = 30.0) -> None:
        self.base_url = (base_url or os.getenv("READER_SERVICE_URL", "http://reader-service:8100")).rstrip("/")
        # httpx.Client is thread-safe; one instance is fine for the whole process.
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    # ── endpoints ────────────────────────────────────────────────────────────
    def list_sources(self) -> list[str]:
        r = self._client.get("/sources")
        r.raise_for_status()
        return list(r.json().get("sources", []))

    def poll(self, source_type: str, tenant_id: int) -> tuple[str | None, list[EventLogPayload]]:
        r = self._client.post(f"/sources/{source_type}/poll", params={"tenant_id": tenant_id})
        r.raise_for_status()
        data = r.json()
        batch_id = data.get("batch_id")
        payloads = [_payload_from_dict(e) for e in data.get("events") or []]
        return batch_id, payloads

    def ack(self, source_type: str, batch_id: str) -> None:
        r = self._client.post(f"/sources/{source_type}/ack", json={"batch_id": batch_id})
        r.raise_for_status()


_PAYLOAD_FIELDS = {f.name for f in fields(EventLogPayload)}


def _payload_from_dict(d: dict[str, Any]) -> EventLogPayload:
    ts = d.get("timestamp")
    if isinstance(ts, str):
        d = {**d, "timestamp": datetime.fromisoformat(ts)}
    # Drop any keys we don't know about (forward-compat with newer readers).
    clean = {k: v for k, v in d.items() if k in _PAYLOAD_FIELDS}
    return EventLogPayload(**clean)
