"""AZURE-MONITOR: reader for AKS container logs in Azure Log Analytics.

Container Insights ships every pod's stdout/stderr to the ContainerLogV2 table,
so one config row covers all components (vote, worker, result, redis, db, ...):

    source_type = azure_container_logs
    key         = namespace to watch, e.g. default
    value       = https://api.loganalytics.io/v1/workspaces/<WORKSPACE-ID>/query

Each poll runs a KQL query for rows ingested after the last watermark. The
watermark is ingestion_time(), not TimeGenerated: rows arrive minutes late, so
a TimeGenerated watermark would skip lines that land after we moved past them.
State shape:
    { "watermarks": { "<key>": "<ISO ingestion time>" } }

Lines are classified with the same parser as kubernetes_pod_logs and written
in the Fluentd shape (level / logger / message), so FluentdSeverityAnalyzer
counts ERROR/WARN per pod (server_id) unchanged.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable

import httpx

from app.readers.azure_auth import LOG_ANALYTICS_SCOPE, azure_token
from app.readers.base import BaseReader, EventLogPayload
from app.readers.k8s_pod_log_reader import (
    _CONTROL, config_service_url, load_config_targets, parse_line,
)
from app.readers.reader_state import load_state, save_state
from app.readers.timestamps import parse_iso_timestamp


log = logging.getLogger(__name__)

# Last query time per (tenant, key); in-memory, same reasoning as k8s_pod_log_reader.
_LAST_FETCH: dict[tuple[int, str], float] = {}

_KQL = """
ContainerLogV2
| where TimeGenerated > datetime({watermark}) - 1d
| where PodNamespace == '{namespace}'
| extend IngestedAt = ingestion_time()
| where IngestedAt > datetime({watermark})
| project IngestedAt, TimeGenerated, PodName, ContainerName, LogMessage = tostring(LogMessage)
| order by IngestedAt asc
| take {max_rows}
"""


class AzureContainerLogsReader(BaseReader):
    source_type = "azure_container_logs"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        self.config_url = config_service_url(self.config)
        self.poll_interval_sec = float(self.config.get("poll_interval_sec", 60))
        self.timeout_sec = float(self.config.get("http_timeout_sec", 30))
        self.max_message_chars = int(self.config.get("max_message_chars", 500))
        self.initial_lookback_min = int(self.config.get("initial_lookback_min", 15))
        self.max_rows = int(self.config.get("max_rows_per_poll", 5000))
        self._pending_watermarks: dict[str, str] = {}

    def read(self) -> Iterable[EventLogPayload]:
        self._pending_watermarks = {}
        targets = load_config_targets(self.config_url, self.tenant_id, self.source_type, self.timeout_sec)
        if not targets:
            return
        watermarks: dict[str, str] = dict(load_state(self.source_type).get("watermarks") or {})

        with httpx.Client(timeout=self.timeout_sec) as client:
            for key, url in targets:
                last = _LAST_FETCH.get((self.tenant_id, key), 0.0)
                if time.monotonic() - last < self.poll_interval_sec:
                    continue
                _LAST_FETCH[(self.tenant_id, key)] = time.monotonic()
                yield from self._read_target(client, key, url, watermarks.get(key))

    def _read_target(self, client: httpx.Client, namespace: str, url: str,
                     watermark: str | None) -> Iterable[EventLogPayload]:
        if not watermark:
            start = datetime.now(timezone.utc) - timedelta(minutes=self.initial_lookback_min)
            watermark = start.isoformat()
        query = _KQL.format(
            namespace=namespace.replace("'", ""),
            watermark=watermark,
            max_rows=self.max_rows,
        )
        try:
            r = client.post(
                url,
                json={"query": query},
                headers={"Authorization": f"Bearer {azure_token(LOG_ANALYTICS_SCOPE)}"},
            )
            r.raise_for_status()
            table = r.json()["tables"][0]
        except Exception as e:
            log.warning("[%s] query failed for %s: %s", self.source_type, namespace, e)
            return

        cols = [c["name"] for c in table["columns"]]
        rows = [dict(zip(cols, row)) for row in table["rows"]]
        if len(rows) >= self.max_rows:
            log.warning("[%s] %s: hit max_rows_per_poll=%d — remaining rows follow next poll",
                        self.source_type, namespace, self.max_rows)

        for row in rows:
            line = row.get("LogMessage") or ""
            if not line.strip():
                continue
            ts = parse_iso_timestamp(row["TimeGenerated"])
            _, level, status = parse_line(line, ts)
            container = row.get("ContainerName")
            yield EventLogPayload(
                tenant_id=self.tenant_id,
                source_type=self.source_type,
                timestamp=ts,
                activity_name="log_event",
                activity_type="automated",
                lifecycle_stage="complete",
                system_id=container,
                server_id=row.get("PodName"),
                metadata_json={
                    "level": level,
                    "logger": container,
                    "message": _CONTROL.sub("", line)[: self.max_message_chars],
                    "namespace": namespace,
                    "pod": row.get("PodName"),
                    "http_status": status,
                },
            )

        if rows:
            self._pending_watermarks[namespace] = rows[-1]["IngestedAt"]

    def commit(self) -> None:
        if not self._pending_watermarks:
            return
        state = load_state(self.source_type)
        watermarks: dict[str, str] = dict(state.get("watermarks") or {})
        watermarks.update(self._pending_watermarks)
        state["watermarks"] = watermarks
        save_state(self.source_type, state)
        self._pending_watermarks = {}
