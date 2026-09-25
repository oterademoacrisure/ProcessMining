from __future__ import annotations

from typing import Any, Sequence

from app.analyzers.base import BaseAnalyzer
from app.db.models import EventLog
from app.findings import Finding


DEFAULTS = {
    # Sudden-spike: if the last reading exceeds (first reading * multiplier)
    # by at least `min_delta`, flag as critical. Targets sudden mass-failure.
    "spike_multiplier": 3.0,
    "spike_min_delta": 10,

    # Sustained-high: a value above this threshold on every reading in batch
    # gets flagged as high.
    "paused_by_exception_sustained_high": 20,

    # Trending-up: monotonic increase with latest above this -> high.
    "trending_up_threshold": 10,
}


def _is_increasing(values: list[float]) -> bool:
    return len(values) >= 2 and all(a <= b for a, b in zip(values, values[1:]))


class AppianProcessMetricsAnalyzer(BaseAnalyzer):
    """Watches the gauge values written by AppianProcessMetricsReader.

    Primary signals are `paused_by_exception_processes` and
    `paused_by_exception_nodes` — a sudden spike here indicates mass
    process failure (often caused by upstream infrastructure pressure).
    """

    KEY_METRICS = ("paused_by_exception_processes", "paused_by_exception_nodes")

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        merged = {**DEFAULTS, **(self.config or {})}
        self.spike_multiplier = float(merged["spike_multiplier"])
        self.spike_min_delta = int(merged["spike_min_delta"])
        self.sustained_high = int(merged["paused_by_exception_sustained_high"])
        self.trending_up = int(merged["trending_up_threshold"])

    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        if not rows:
            return []

        rows = sorted(rows, key=lambda r: r.timestamp)
        per_metric: dict[str, list[int]] = {m: [] for m in self.KEY_METRICS}
        for r in rows:
            meta = r.metadata_json or {}
            for m in self.KEY_METRICS:
                v = meta.get(m)
                if isinstance(v, (int, float)):
                    per_metric[m].append(int(v))

        worst_severity = "normal"
        observations: list[str] = []
        per_metric_results: dict[str, dict] = {}

        for metric, values in per_metric.items():
            if not values:
                per_metric_results[metric] = {"count": 0, "severity": "normal"}
                continue

            first, last = values[0], values[-1]
            peak = max(values)
            spike = (
                last >= self.spike_min_delta
                and first > 0
                and last >= first * self.spike_multiplier
            ) or (first == 0 and last >= self.spike_min_delta)
            sustained = all(v > self.sustained_high for v in values)
            trending = _is_increasing(values) and last > self.trending_up

            if spike:
                severity = "critical"
                obs = f"Sudden spike in {metric}: {first} -> {last} (peak {peak})"
            elif sustained:
                severity = "critical"
                obs = f"Sustained high {metric}: every reading > {self.sustained_high} (peak {peak})"
            elif trending:
                severity = "high"
                obs = f"{metric} trending up to {last}"
            else:
                severity = "normal"
                obs = None

            per_metric_results[metric] = {
                "first":   first,
                "last":    last,
                "peak":    peak,
                "count":   len(values),
                "severity": severity,
                "observation": obs,
            }
            if _rank(severity) > _rank(worst_severity):
                worst_severity = severity
            if obs:
                observations.append(obs)

        payload = {
            "record_count": len(rows),
            "metrics":      per_metric_results,
            "severity":     worst_severity,
            "observation":  "; ".join(observations) if observations else "No mass-failure pattern detected",
        }
        return [Finding(
            severity=worst_severity,
            subject_key="appian_process_engine",
            observation=payload["observation"],
            payload=payload,
            contributing_rows=list(rows),
        )]


def _rank(severity: str) -> int:
    return {"normal": 0, "high": 1, "critical": 2}.get(severity, 0)
