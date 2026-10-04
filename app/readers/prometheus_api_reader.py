"""AZURE-MONITOR: Prometheus reader that queries a live Prometheus HTTP API.

One config row per namespace:

    source_type = prometheus
    key         = namespace to watch, e.g. default
    value       = Prometheus query endpoint, e.g.
                  https://<name>.<region>.prometheus.monitor.azure.com  (managed)
                  http://prometheus:9090                                  (self-hosted)

Azure Monitor managed Prometheus needs an Entra ID token (Monitoring Data
Reader role); any other host is called without auth.

Each poll emits one metric_sample per pod with cpu_usage_pct / memory_usage_pct
(usage against the container limits), so PrometheusMetricsAnalyzer works
unchanged. server_id = system_id = pod, the same server_id the log readers use,
so metric and log findings for a pod line up.

No config rows -> falls back to the file-based PrometheusReader, so the demo
data in data/input/prometheus keeps working.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Iterable
from urllib.parse import urlsplit

import httpx

from app.readers.azure_auth import PROMETHEUS_SCOPE, azure_token
from app.readers.base import EventLogPayload
from app.readers.k8s_pod_log_reader import config_service_url, load_config_targets
from app.readers.prometheus_reader import PrometheusReader


log = logging.getLogger(__name__)

# Last query time per (tenant, key); in-memory, same reasoning as k8s_pod_log_reader.
_LAST_FETCH: dict[tuple[int, str], float] = {}

_CONTAINERS = 'namespace="{ns}", container!="", container!="POD"'
_QUERIES = {
    "cpu_usage_pct": (
        "100 * sum by (pod) (rate(container_cpu_usage_seconds_total{%s}[5m]))"
        ' / sum by (pod) (kube_pod_container_resource_limits{namespace="{ns}", resource="cpu"})'
        % _CONTAINERS
    ),
    "memory_usage_pct": (
        "100 * sum by (pod) (container_memory_working_set_bytes{%s})"
        ' / sum by (pod) (kube_pod_container_resource_limits{namespace="{ns}", resource="memory"})'
        % _CONTAINERS
    ),
    "restarts_total": 'sum by (pod) (kube_pod_container_status_restarts_total{namespace="{ns}"})',
}


class PrometheusApiReader(PrometheusReader):
    source_type = "prometheus"

    def __init__(self, tenant_id: int, config: dict | None = None):
        super().__init__(tenant_id=tenant_id, config=config)
        self.config_url = config_service_url(self.config)
        self.api_poll_interval_sec = float(self.config.get("api_poll_interval_sec", 60))
        self.timeout_sec = float(self.config.get("http_timeout_sec", 15))

    def read(self) -> Iterable[EventLogPayload]:
        targets = load_config_targets(self.config_url, self.tenant_id, self.source_type, self.timeout_sec)
        if not targets:
            yield from super().read()
            return

        self._pending_files = []
        with httpx.Client(timeout=self.timeout_sec) as client:
            for key, url in targets:
                last = _LAST_FETCH.get((self.tenant_id, key), 0.0)
                if time.monotonic() - last < self.api_poll_interval_sec:
                    continue
                _LAST_FETCH[(self.tenant_id, key)] = time.monotonic()
                yield from self._read_target(client, key, url.rstrip("/"))

    def _read_target(self, client: httpx.Client, namespace: str,
                     base_url: str) -> Iterable[EventLogPayload]:
        headers = {}
        if (urlsplit(base_url).hostname or "").endswith(".prometheus.monitor.azure.com"):
            try:
                headers["Authorization"] = f"Bearer {azure_token(PROMETHEUS_SCOPE)}"
            except Exception as e:
                log.warning("[%s] could not get Azure token for %s: %s", self.source_type, namespace, e)
                return

        ns = namespace.replace('"', "")
        per_pod: dict[str, dict] = {}
        for metric, promql in _QUERIES.items():
            try:
                r = client.get(f"{base_url}/api/v1/query",
                               params={"query": promql.replace("{ns}", ns)}, headers=headers)
                r.raise_for_status()
                results = r.json()["data"]["result"]
            except Exception as e:
                log.warning("[%s] query %s failed for %s: %s", self.source_type, metric, namespace, e)
                continue
            for item in results:
                pod = item["metric"].get("pod")
                if pod:
                    per_pod.setdefault(pod, {})[metric] = float(item["value"][1])

        scraped_at = datetime.now(timezone.utc)
        for pod, values in sorted(per_pod.items()):
            cpu = values.get("cpu_usage_pct")
            mem = values.get("memory_usage_pct")
            yield EventLogPayload(
                tenant_id=self.tenant_id,
                source_type=self.source_type,
                timestamp=scraped_at,
                activity_name="metric_sample",
                activity_type="automated",
                lifecycle_stage="complete",
                system_id=pod,
                server_id=pod,
                metadata_json={
                    "system_id":        pod,
                    "application_name": namespace,
                    "hostname":         pod,
                    "cpu_usage_pct":    int(round(cpu)) if cpu is not None else None,
                    "memory_usage_pct": int(round(mem)) if mem is not None else None,
                    "restarts_total":   int(values["restarts_total"]) if "restarts_total" in values else None,
                    "slow_query_count": 0,
                    "wait_type":        "CPU" if (cpu or 0) > 50 else "None",
                    "namespace":        namespace,
                },
            )
