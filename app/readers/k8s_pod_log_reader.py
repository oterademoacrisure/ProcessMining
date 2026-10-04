"""K8S-POD-LOGS: reader for Kubernetes pod logs exposed over HTTP.

Targets are NOT in modules.yaml — operators add them on the Source
Configuration page (config-service), one row per component:

    source_type = kubernetes_pod_logs
    key         = component, e.g. vote          -> system_id
    value       = URL returning {"app", "namespace", "pod", "logs": "<lines>"}

Each poll asks config-service for the rows, fetches every component whose
poll interval has elapsed, and yields one event per NEW log line.

The endpoints return the last N lines (tail=N) with no cursor, so each fetch
overlaps the previous one. We remember the hashes of the last window per
component and emit only the lines after the overlap. State shape:
    { "windows": { "<key>": { "pod": "<pod>", "hashes": [...] }, ... } }

Each event's metadata uses the Fluentd shape (level / logger / message), so
FluentdSeverityAnalyzer classifies it without changes.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Iterable

import httpx

from app.readers.base import BaseReader, EventLogPayload
from app.readers.reader_state import load_state, save_state


log = logging.getLogger(__name__)

# Line formats seen from the voting-app stack. Anything else falls back to
# keyword matching and the fetch time.
_WERKZEUG = re.compile(r'\[(\d{2}/\w{3}/\d{4} \d{2}:\d{2}:\d{2})\] "[^"]*" (\d{3})')
# AZURE-MONITOR: uvicorn access line (logs-proxy), e.g.
#   INFO:     10.224.0.5:52994 - "GET /x HTTP/1.0" 404 Not Found
_UVICORN = re.compile(r'^[A-Z]+:\s+\S+ - "[^"]*" (\d{3})\b')
_REDIS = re.compile(r"^\d+:[A-Z] (\d{2} \w{3} \d{4} \d{2}:\d{2}:\d{2})\.\d+ ([.\-*#])")
_POSTGRES = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d+ UTC \[\d+\] (\w+):")
_ERROR_WORDS = re.compile(r"\b(exception|error|fatal|panic|traceback|refused|failed)\b", re.I)
_WARN_WORDS = re.compile(r"\bwarn(ing)?\b", re.I)
# ANSI colour codes and other control bytes (scanner payloads are full of them).
_CONTROL = re.compile(r"\x1b\[[0-9;]*m|[\x00-\x08\x0b-\x1f\x7f]")

_PG_LEVELS = {"ERROR": "ERROR", "FATAL": "ERROR", "PANIC": "ERROR", "WARNING": "WARN"}

# Last fetch time per (tenant, key). In-memory on purpose: readers are rebuilt
# on every poll, and after a restart an immediate fetch is harmless.
_LAST_FETCH: dict[tuple[int, str], float] = {}


def _line_hash(line: str) -> str:
    return hashlib.md5(line.encode("utf-8", "replace")).hexdigest()[:16]


def _overlap(old: list[str], new: list[str]) -> int:
    """Length of the longest suffix of `old` that equals a prefix of `new`."""
    for k in range(min(len(old), len(new)), 0, -1):
        if old[-k:] == new[:k]:
            return k
    return 0


def parse_line(line: str, fetched_at: datetime) -> tuple[datetime, str, int | None]:
    """Return (timestamp, level, http_status) for one raw log line."""
    m = _WERKZEUG.search(line)
    if m:
        ts = datetime.strptime(m.group(1), "%d/%b/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
        status = int(m.group(2))
        # 4xx is mostly scanner noise on public endpoints; only 5xx is our fault.
        return ts, ("ERROR" if status >= 500 else "INFO"), status
    m = _UVICORN.match(line)
    if m:
        # No timestamp in the line; same 5xx-only rule as werkzeug.
        status = int(m.group(1))
        return fetched_at, ("ERROR" if status >= 500 else "INFO"), status
    m = _REDIS.match(line)
    if m:
        ts = datetime.strptime(m.group(1), "%d %b %Y %H:%M:%S").replace(tzinfo=timezone.utc)
        return ts, ("WARN" if m.group(2) == "#" else "INFO"), None
    m = _POSTGRES.match(line)
    if m:
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return ts, _PG_LEVELS.get(m.group(2), "INFO"), None
    if _ERROR_WORDS.search(line):
        return fetched_at, "ERROR", None
    if _WARN_WORDS.search(line):
        return fetched_at, "WARN", None
    return fetched_at, "INFO", None


def config_service_url(config: dict) -> str:
    return (config.get("config_service_url")
            or os.getenv("CONFIG_SERVICE_URL", "http://localhost:8200")).rstrip("/")


# AZURE-MONITOR: module-level so the Azure Monitor readers share it.
def load_config_targets(config_url: str, tenant_id: int, source_type: str,
                        timeout_sec: float) -> list[tuple[str, str]]:
    # Fail-safe: config-service down means "nothing to read this cycle".
    try:
        r = httpx.get(
            f"{config_url}/configs",
            params={"tenant_id": tenant_id, "source_type": source_type},
            timeout=timeout_sec,
        )
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        log.warning("[%s] could not load targets from config-service: %s", source_type, e)
        return []
    return [(row["key"], row["value"]) for row in rows]


class K8sPodLogReader(BaseReader):
    source_type = "kubernetes_pod_logs"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        self.config_url = config_service_url(self.config)
        self.poll_interval_sec = float(self.config.get("poll_interval_sec", 60))
        self.timeout_sec = float(self.config.get("http_timeout_sec", 10))
        self.max_message_chars = int(self.config.get("max_message_chars", 500))
        self._pending_windows: dict[str, dict] = {}

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_windows = {}
        targets = self._load_targets()
        if not targets:
            return
        windows: dict[str, dict] = dict(load_state(self.source_type).get("windows") or {})

        with httpx.Client(timeout=self.timeout_sec) as client:
            for key, url in targets:
                last = _LAST_FETCH.get((self.tenant_id, key), 0.0)
                if time.monotonic() - last < self.poll_interval_sec:
                    continue
                _LAST_FETCH[(self.tenant_id, key)] = time.monotonic()
                yield from self._read_target(client, key, url, windows.get(key) or {})

    def _load_targets(self) -> list[tuple[str, str]]:
        return load_config_targets(self.config_url, self.tenant_id, self.source_type, self.timeout_sec)

    def _read_target(self, client: httpx.Client, key: str, url: str,
                     window: dict) -> Iterable[EventLogPayload]:
        try:
            r = client.get(url)
            r.raise_for_status()
            body = r.json()
        except Exception as e:
            log.warning("[%s] fetch failed for %s: %s", self.source_type, key, e)
            return

        fetched_at = datetime.now(timezone.utc)
        pod = body.get("pod")
        lines = [ln for ln in (body.get("logs") or "").splitlines() if ln.strip()]
        hashes = [_line_hash(ln) for ln in lines]

        # A new pod (restart / rollout) starts a fresh window.
        old = (window.get("hashes") or []) if window.get("pod") == pod else []
        start = _overlap(old, hashes)
        if old and start == 0:
            log.warning("[%s] %s: no overlap with previous fetch — lines may have been "
                        "missed; raise tail or poll more often", self.source_type, key)

        for line in lines[start:]:
            ts, level, status = parse_line(line, fetched_at)
            yield EventLogPayload(
                tenant_id=self.tenant_id,
                source_type=self.source_type,
                timestamp=ts,
                activity_name="log_event",
                activity_type="automated",
                lifecycle_stage="complete",
                system_id=key,
                server_id=pod,
                metadata_json={
                    "level": level,
                    "logger": key,
                    "message": _CONTROL.sub("", line)[: self.max_message_chars],
                    "namespace": body.get("namespace"),
                    "pod": pod,
                    "http_status": status,
                },
            )

        self._pending_windows[key] = {"pod": pod, "hashes": hashes}

    def commit(self) -> None:
        if not self._pending_windows:
            return
        state = load_state(self.source_type)
        windows: dict[str, dict] = dict(state.get("windows") or {})
        windows.update(self._pending_windows)
        state["windows"] = windows
        save_state(self.source_type, state)
        self._pending_windows = {}
