"""APIFICATION: in-memory pending-batch store for the reader service.

Preserves the at-least-once contract from app/runner.py: a batch is held in
memory after `read()` and is only `commit()`-ed (i.e. reader state advanced)
when the core acknowledges it. Crash / network loss before ack = redelivery
on the next poll, exactly like the single-process runner.
"""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from app.readers.base import BaseReader, EventLogPayload


@dataclass
class _PendingBatch:
    reader: BaseReader
    payloads: list[EventLogPayload]


class BatchStore:
    """One instance per reader process. Keyed by (source_type, tenant_id).

    Only one outstanding batch per (source_type, tenant_id) is kept at any
    time; a second `poll` before the first is `ack`-ed silently discards the
    first (matches the current runner semantics where two back-to-back
    `read()` calls on the same reader also lose the first result unless it
    was persisted in between).
    """

    def __init__(self) -> None:
        self._pending: dict[str, _PendingBatch] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _make_id(source_type: str, tenant_id: int) -> str:
        return f"{source_type}-{tenant_id}-{uuid.uuid4().hex}"

    def register(
        self, source_type: str, tenant_id: int, reader: BaseReader, payloads: list[EventLogPayload]
    ) -> str:
        batch_id = self._make_id(source_type, tenant_id)
        with self._lock:
            self._pending[batch_id] = _PendingBatch(reader=reader, payloads=payloads)
        return batch_id

    def ack(self, batch_id: str) -> bool:
        """Advance the reader's checkpoint and drop the batch. Returns False
        if the batch is unknown (already acked, or never existed)."""
        with self._lock:
            pending = self._pending.pop(batch_id, None)
        if pending is None:
            return False
        try:
            pending.reader.commit()
        except Exception:
            # Reader.commit failed — put the batch back so the caller can retry.
            with self._lock:
                self._pending[batch_id] = pending
            raise
        return True

    def has(self, batch_id: str) -> bool:
        with self._lock:
            return batch_id in self._pending

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)


# Module-level singleton — the reader service is a single process per container.
STORE = BatchStore()


def payload_to_dict(p: EventLogPayload) -> dict[str, Any]:
    """Serialize an EventLogPayload for JSON transport. datetimes -> isoformat."""
    from dataclasses import asdict

    d = asdict(p)
    ts = d.get("timestamp")
    if ts is not None and hasattr(ts, "isoformat"):
        d["timestamp"] = ts.isoformat()
    return d
