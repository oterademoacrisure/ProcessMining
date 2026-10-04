"""AZURE-MONITOR: replay an export from collect-azure-monitor.ps1 through the
real readers and analyzers, without any Azure credentials on this machine.

The export holds the raw API responses (10_reader_replay.json from Log
Analytics, 81-83_prom_*.json from Prometheus). They are served to
AzureContainerLogsReader / PrometheusApiReader through an httpx mock, so the
same parsing, classification and analysis code runs as in production.
Nothing is written to the database or to data/state.

    python scripts/replay_azure_export.py <export folder or .zip>
"""
from __future__ import annotations

import json
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

import app.readers.azure_container_logs_reader as la
import app.readers.prometheus_api_reader as pa
from app.analyzers.fluentd_severity_analyzer import FluentdSeverityAnalyzer
from app.analyzers.prometheus_metrics_analyzer import PrometheusMetricsAnalyzer

LA_URL = "https://api.loganalytics.io/v1/workspaces/replay/query"
PROM_URL = "https://replay.eastus.prometheus.monitor.azure.com"
_PROM_FILES = {"cpu_usage": "81_prom_cpu_usage_pct", "memory_working_set": "82_prom_memory_usage_pct",
               "restarts_total": "83_prom_restarts_total"}


def _open(path: Path) -> Path:
    if path.suffix == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="azexport-"))
        zipfile.ZipFile(path).extractall(tmp)
        return tmp
    return path


def _load(folder: Path, name: str):
    f = folder / f"{name}.json"
    return json.loads(f.read_text(encoding="utf-8-sig")) if f.exists() else None


def _rows(events):
    return [SimpleNamespace(**e.__dict__) for e in events]


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    folder = _open(Path(sys.argv[1]))
    print(f"export: {folder}")
    for f in sorted(folder.glob("*.error.txt")):
        print(f"  (export error) {f.stem}: {f.read_text(encoding='utf-8-sig').strip()[:160]}")

    def handler(req: httpx.Request) -> httpx.Response:
        if "loganalytics" in req.url.host:
            body = _load(folder, "10_reader_replay")
            return httpx.Response(200, json=body) if body else httpx.Response(404)
        q = req.url.params.get("query", "")
        for needle, name in _PROM_FILES.items():
            if needle in q:
                body = _load(folder, name)
                return httpx.Response(200, json=body) if body else httpx.Response(403)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    state: dict = {}
    with mock.patch("httpx.Client", lambda *a, **k: real_client(*a, transport=transport, **k)), \
         mock.patch.object(la, "azure_token", lambda s: "replay"), \
         mock.patch.object(pa, "azure_token", lambda s: "replay"), \
         mock.patch.object(la, "load_config_targets", lambda *a: [("default", LA_URL)]), \
         mock.patch.object(pa, "load_config_targets", lambda *a: [("default", PROM_URL)]), \
         mock.patch.object(la, "load_state", lambda st: dict(state.get(st, {}))), \
         mock.patch.object(la, "save_state", lambda st, v: state.__setitem__(st, v)):

        print("\n=== Azure Log Analytics -> AzureContainerLogsReader ===")
        events = list(la.AzureContainerLogsReader(1, {}).read())
        print(f"events: {len(events)}")
        for (c, lvl), n in sorted(Counter((e.system_id, e.metadata_json["level"]) for e in events).items()):
            print(f"  {c:<15} {lvl:<5} {n}")
        for e in [e for e in events if e.metadata_json["level"] == "ERROR"][-8:]:
            print(f"  ERROR {e.timestamp:%H:%M:%S} {e.server_id}: {e.metadata_json['message'][:110]}")
        print("findings (FluentdSeverityAnalyzer):")
        for f in FluentdSeverityAnalyzer().analyze(_rows(events)):
            print(f"  [{f.severity:<8}] {f.subject_key}: {f.observation}")

        print("\n=== Managed Prometheus -> PrometheusApiReader ===")
        events = list(pa.PrometheusApiReader(1, {}).read())
        print(f"pods: {len(events)}")
        for e in events:
            m = e.metadata_json
            print(f"  {e.server_id:<42} cpu {m['cpu_usage_pct']}%  mem {m['memory_usage_pct']}%  restarts {m['restarts_total']}")
        if events:
            print("findings (PrometheusMetricsAnalyzer):")
            for f in PrometheusMetricsAnalyzer().analyze(_rows(events)):
                print(f"  [{f.severity:<8}] {f.subject_key}: {f.observation}")
    print()


if __name__ == "__main__":
    main()
