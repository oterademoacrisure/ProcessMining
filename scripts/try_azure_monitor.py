"""AZURE-MONITOR: dry run of the Azure Monitor readers against real Azure.

Runs AzureContainerLogsReader and PrometheusApiReader once with the given
URLs (no config-service, no database), then feeds the events to their
analyzers and prints what the pipeline would see. Reader state is kept in
memory, so the real watermark in data/state is not moved.

Auth is DefaultAzureCredential: the AZURE_* service principal in .env if set,
otherwise your `az login`.

    python scripts/try_azure_monitor.py \
        --logs-url https://api.loganalytics.io/v1/workspaces/<id>/query \
        --prom-url https://<amw>.eastus.prometheus.monitor.azure.com \
        --namespace default --lookback-min 60
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter
from types import SimpleNamespace

import app.readers.azure_container_logs_reader as la
import app.readers.prometheus_api_reader as pa
from app.analyzers.fluentd_severity_analyzer import FluentdSeverityAnalyzer
from app.analyzers.prometheus_metrics_analyzer import PrometheusMetricsAnalyzer


def _rows(events):
    return [SimpleNamespace(**e.__dict__) for e in events]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--logs-url", required=True)
    p.add_argument("--prom-url")
    p.add_argument("--namespace", default="default")
    p.add_argument("--lookback-min", type=int, default=60)
    args = p.parse_args()
    logging.basicConfig(level=logging.WARNING, format="  %(levelname)s %(message)s")

    # In-memory state + fixed targets: no config-service, real watermark untouched.
    state: dict = {}
    la.load_state = lambda st: dict(state.get(st, {}))
    la.save_state = lambda st, v: state.__setitem__(st, v)

    print(f"\n=== Azure Log Analytics  ({args.namespace}, last {args.lookback_min} min) ===")
    la.load_config_targets = lambda *a: [(args.namespace, args.logs_url)]
    reader = la.AzureContainerLogsReader(1, {"initial_lookback_min": args.lookback_min})
    events = list(reader.read())
    print(f"events read: {len(events)}")
    if events:
        levels = Counter((e.system_id, e.metadata_json["level"]) for e in events)
        for (container, level), n in sorted(levels.items()):
            print(f"  {container:<15} {level:<5} {n}")
        errors = [e for e in events if e.metadata_json["level"] == "ERROR"][-5:]
        if errors:
            print("latest ERROR lines:")
            for e in errors:
                print(f"  {e.timestamp:%H:%M:%S} {e.server_id}: {e.metadata_json['message'][:120]}")
        print("findings (FluentdSeverityAnalyzer):")
        for f in FluentdSeverityAnalyzer().analyze(_rows(events)):
            print(f"  [{f.severity}] {f.subject_key}: {f.observation}")

    if args.prom_url:
        print(f"\n=== Prometheus  ({args.namespace}) ===")
        pa.load_config_targets = lambda *a: [(args.namespace, args.prom_url)]
        events = list(pa.PrometheusApiReader(1, {}).read())
        print(f"pods sampled: {len(events)}")
        for e in events:
            m = e.metadata_json
            print(f"  {e.server_id:<40} cpu {m['cpu_usage_pct']}%  mem {m['memory_usage_pct']}%  restarts {m['restarts_total']}")
        if events:
            print("findings (PrometheusMetricsAnalyzer):")
            for f in PrometheusMetricsAnalyzer().analyze(_rows(events)):
                print(f"  [{f.severity}] {f.subject_key}: {f.observation}")
    print()


if __name__ == "__main__":
    main()
