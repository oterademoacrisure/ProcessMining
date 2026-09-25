from __future__ import annotations

from typing import Any, Sequence

from app.analyzers.base import BaseAnalyzer
from app.db.models import EventLog
from app.findings import Finding


DEFAULTS = {
    # Sustained-high (rolling window): N consecutive readings above threshold
    # = critical. Detects multi-minute saturation even when the batch includes
    # pre- and post-incident recovery.
    "sustained_cpu_threshold":      78,
    "sustained_mem_threshold":      78,
    "sustained_min_consecutive":    3,

    # Peak: any single reading at this level is treated as critical regardless
    # of duration. Catches single-snapshot batches (e.g., simulator ticks) and
    # short but severe spikes (CPU = 99%).
    "peak_cpu_threshold":           90,
    "peak_mem_threshold":           90,

    # Transient spike: high peak but NOT sustained -> flag as 'high' not
    # critical. Keeps the old spike-detection behavior.
    "spike_cpu_threshold":          85,

    # Trending-up: monotonic increase with latest above this -> 'high'.
    "trending_up_threshold":        70,

    # Per-source: very high slow-query count on the latest reading.
    "slow_query_high_threshold":    10,
}


def _detect_trend(values: list[float]) -> str:
    if len(values) < 2:
        return "insufficient_data"
    if all(x <= y for x, y in zip(values, values[1:])):
        return "increasing"
    if all(x >= y for x, y in zip(values, values[1:])):
        return "decreasing"
    return "fluctuating"


def _longest_run_above(values: list[float], threshold: float) -> int:
    """Length of the longest contiguous run of values strictly above threshold."""
    longest = 0
    current = 0
    for v in values:
        if v is None:
            current = 0
            continue
        if v > threshold:
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return longest


def _peak_above(values: list[float], threshold: float) -> bool:
    return any(v is not None and v > threshold for v in values)


class PrometheusMetricsAnalyzer(BaseAnalyzer):
    """Analyzes Prometheus-shaped metric samples (cpu_usage_pct, memory_usage_pct,
    etc. inside metadata_json). Reused for the simulator source which emits the
    same shape.

    Detection logic:
      - Sustained-high: any contiguous run of N readings above threshold
        (rolling-window — detects saturation even when the batch includes
        recovery, unlike a plain `all(...)` check).
      - Peak-critical: any single reading above a higher threshold (catches
        very-high one-shot snapshots and single-tick batches).
      - Spike (transient): peak above the spike threshold but not sustained ->
        'high' severity.
      - Trending-up: monotonic increase with latest above threshold -> 'high'.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        merged = {**DEFAULTS, **(self.config or {})}
        self.sustained_cpu        = int(merged["sustained_cpu_threshold"])
        self.sustained_mem        = int(merged["sustained_mem_threshold"])
        self.sustained_min_run    = int(merged["sustained_min_consecutive"])
        self.peak_cpu             = int(merged["peak_cpu_threshold"])
        self.peak_mem             = int(merged["peak_mem_threshold"])
        self.spike_cpu            = int(merged["spike_cpu_threshold"])
        self.trending_up          = int(merged["trending_up_threshold"])
        self.slow_query_high      = int(merged["slow_query_high_threshold"])

    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        if not rows:
            return []

        groups: dict[str, list[EventLog]] = {}
        for r in rows:
            key = r.system_id or "_unknown"
            groups.setdefault(key, []).append(r)

        findings: list[Finding] = []
        for sid, group in groups.items():
            payload = self._analyze_app(sid, group)
            findings.append(Finding(
                severity=payload["severity"],
                subject_key=sid,
                observation=payload.get("observation"),
                payload=payload,
                contributing_rows=group,
            ))
        return findings

    def _analyze_app(self, system_id: str, rows: list[EventLog]) -> dict[str, Any]:
        rows = sorted(rows, key=lambda r: r.timestamp)
        metas = [r.metadata_json or {} for r in rows]

        cpu_values  = [v for v in (m.get("cpu_usage_pct")      for m in metas) if v is not None]
        mem_values  = [v for v in (m.get("memory_usage_pct")   for m in metas) if v is not None]
        conn_values = [v for v in (m.get("active_connections") for m in metas) if v is not None]
        slow_values = [v for v in (m.get("slow_query_count")   for m in metas) if v is not None]

        latest_meta  = metas[-1]
        latest_cpu   = cpu_values[-1]  if cpu_values  else None
        latest_mem   = mem_values[-1]  if mem_values  else None
        latest_slow  = slow_values[-1] if slow_values else 0

        # Effective required-run-length: don't demand 3 consecutive readings
        # when the batch only has 1. Fall back to len(values) for short batches.
        eff_run_cpu = min(self.sustained_min_run, max(1, len(cpu_values)))
        eff_run_mem = min(self.sustained_min_run, max(1, len(mem_values)))

        cpu_run = _longest_run_above(cpu_values, self.sustained_cpu) if cpu_values else 0
        mem_run = _longest_run_above(mem_values, self.sustained_mem) if mem_values else 0

        cpu_sustained_high = cpu_run >= eff_run_cpu and cpu_run > 0
        mem_sustained_high = mem_run >= eff_run_mem and mem_run > 0
        cpu_peak_critical  = _peak_above(cpu_values, self.peak_cpu)
        mem_peak_critical  = _peak_above(mem_values, self.peak_mem)

        cpu_trend = _detect_trend(cpu_values)
        mem_trend = _detect_trend(mem_values)
        cpu_spike = (
            bool(cpu_values)
            and max(cpu_values) > self.spike_cpu
            and not cpu_sustained_high
            and not cpu_peak_critical
        )

        result: dict[str, Any] = {
            "system_id":           system_id,
            "application_name":    latest_meta.get("application_name"),
            "record_count":        len(rows),
            "cpu_trend":           cpu_trend,
            "memory_trend":        mem_trend,
            "cpu_sustained_high":  cpu_sustained_high,
            "mem_sustained_high":  mem_sustained_high,
            "cpu_peak_critical":   cpu_peak_critical,
            "mem_peak_critical":   mem_peak_critical,
            "cpu_run_length":      cpu_run,
            "mem_run_length":      mem_run,
            "cpu_spike":           cpu_spike,
            "avg_cpu":             round(sum(cpu_values) / len(cpu_values), 1) if cpu_values else None,
            "avg_memory":          round(sum(mem_values) / len(mem_values), 1) if mem_values else None,
            "avg_connections":     round(sum(conn_values) / len(conn_values), 1) if conn_values else None,
            "max_cpu":             max(cpu_values) if cpu_values else None,
            "max_memory":          max(mem_values) if mem_values else None,
            "latest_cpu":          latest_cpu,
            "latest_memory":       latest_mem,
            "latest_connections":  conn_values[-1] if conn_values else None,
            "latest_slow_queries": latest_slow,
            "wait_type":           latest_meta.get("wait_type"),
            "observation":         None,
            "severity":            "normal",
        }

        cpu_bad = cpu_sustained_high or cpu_peak_critical
        mem_bad = mem_sustained_high or mem_peak_critical

        if cpu_bad and mem_bad:
            result["observation"] = self._observation_both(cpu_run, mem_run, result["max_cpu"], result["max_memory"])
            result["severity"]    = "critical"
        elif cpu_bad:
            result["observation"] = self._observation_one("CPU", cpu_run, result["max_cpu"], cpu_sustained_high)
            result["severity"]    = "critical"
        elif mem_bad:
            result["observation"] = self._observation_one("memory", mem_run, result["max_memory"], mem_sustained_high)
            result["severity"]    = "critical"
        elif cpu_trend == "increasing" and (latest_cpu or 0) > self.trending_up:
            result["observation"] = "CPU trending up towards critical"
            result["severity"]    = "high"
        elif mem_trend == "increasing" and (latest_mem or 0) > self.trending_up:
            result["observation"] = "Memory trending up — watch for leak"
            result["severity"]    = "high"
        elif latest_slow > self.slow_query_high:
            result["observation"] = f"High slow query count: {latest_slow}"
            result["severity"]    = "high"
        elif cpu_spike:
            result["observation"] = f"Transient CPU spike (peak {result['max_cpu']}%) without sustained pattern"
            result["severity"]    = "high"
        else:
            result["observation"] = "No critical pattern detected"
            result["severity"]    = "normal"

        return result

    def _observation_one(self, kind: str, run: int, peak, sustained: bool) -> str:
        if sustained and run >= self.sustained_min_run:
            return f"Sustained high {kind} ({run} consecutive readings above threshold, peak {peak}%)"
        return f"{kind.capitalize()} peak {peak}% above critical threshold"

    def _observation_both(self, cpu_run: int, mem_run: int, cpu_peak, mem_peak) -> str:
        return (
            f"Sustained high CPU and memory — likely resource exhaustion "
            f"(CPU peak {cpu_peak}%, memory peak {mem_peak}%, "
            f"runs: cpu={cpu_run} mem={mem_run})"
        )
