from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from app.analyzers.base import BaseAnalyzer
from app.db.models import EventLog
from app.findings import Finding


DEFAULTS = {
    "error_critical_threshold": 5,
    "error_high_threshold": 1,
    "warn_high_threshold": 5,
}


class FluentdSeverityAnalyzer(BaseAnalyzer):
    """Counts ERROR/WARN events per server_id and classifies severity.

    Output is per-host: level counts, top error loggers, sample error events
    (for the investigator to drill into), severity, observation.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        merged = {**DEFAULTS, **(self.config or {})}
        self.error_critical = merged["error_critical_threshold"]
        self.error_high = merged["error_high_threshold"]
        self.warn_high = merged["warn_high_threshold"]

    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        if not rows:
            return []

        groups: dict[str, list[EventLog]] = {}
        for r in rows:
            key = r.server_id or "_unknown"
            groups.setdefault(key, []).append(r)

        findings: list[Finding] = []
        for host, group in groups.items():
            payload = self._analyze_host(host, group)
            findings.append(Finding(
                severity=payload["severity"],
                subject_key=host,
                observation=payload.get("observation"),
                payload=payload,
                contributing_rows=group,
            ))
        return findings

    def _analyze_host(self, host: str, rows: list[EventLog]) -> dict[str, Any]:
        levels: Counter[str] = Counter()
        error_loggers: Counter[str] = Counter()
        sample_errors: list[dict] = []

        for r in rows:
            meta = r.metadata_json or {}
            level = (meta.get("level") or "").upper()
            levels[level] += 1
            if level == "ERROR":
                error_loggers[meta.get("logger") or "unknown"] += 1
                if len(sample_errors) < 5:
                    sample_errors.append({
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "logger": meta.get("logger"),
                        "message": meta.get("message"),
                    })

        n_errors = levels.get("ERROR", 0)
        n_warns = levels.get("WARN", 0)

        result: dict[str, Any] = {
            "server_id":          host,
            "record_count":       len(rows),
            "level_counts":       dict(levels),
            "top_error_loggers":  dict(error_loggers.most_common(3)),
            "sample_errors":      sample_errors,
            "severity":           "normal",
            "observation":        None,
        }

        if n_errors >= self.error_critical:
            result["severity"] = "critical"
            result["observation"] = (
                f"{n_errors} ERROR events on {host} (threshold {self.error_critical})"
            )
        elif n_errors >= self.error_high:
            result["severity"] = "high"
            result["observation"] = f"{n_errors} ERROR event(s) on {host}"
        elif n_warns >= self.warn_high:
            result["severity"] = "high"
            result["observation"] = f"{n_warns} WARN events on {host}"
        else:
            result["observation"] = f"No significant error pattern on {host}"

        return result
