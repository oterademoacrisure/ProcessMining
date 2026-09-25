from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Sequence

from app.analyzers.base import BaseAnalyzer
from app.db.models import EventLog
from app.findings import Finding


DEFAULTS = {
    "timeout_critical_count": 3,        # >= N timeouts on one service => critical
    "failure_high_rate_pct": 25.0,      # failure rate >= 25% on one service => high
    "slow_threshold_ms": 5000,          # individual call >5s flagged in metadata
    "slow_high_count": 5,               # >5 slow calls on one service => high
}


class PegaIntegrationAnalyzer(BaseAnalyzer):
    """Per-service analyzer for Pega integration-trace rows.

    Groups EVENT_LOG rows by Pega service name and counts timeouts, HTTP
    failures, and slow calls; classifies severity per service.
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        merged = {**DEFAULTS, **(self.config or {})}
        self.timeout_critical = int(merged["timeout_critical_count"])
        self.failure_high_rate = float(merged["failure_high_rate_pct"])
        self.slow_threshold_ms = float(merged["slow_threshold_ms"])
        self.slow_high_count = int(merged["slow_high_count"])

    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        if not rows:
            return []

        groups: dict[str, list[EventLog]] = {}
        for r in rows:
            meta = r.metadata_json or {}
            key = meta.get("service_name") or meta.get("operation") or "_unknown"
            groups.setdefault(key, []).append(r)

        findings: list[Finding] = []
        for key, group in groups.items():
            payload = self._analyze_service(key, group)
            findings.append(Finding(
                severity=payload["severity"],
                subject_key=key,
                observation=payload.get("observation"),
                payload=payload,
                contributing_rows=group,
            ))
        return findings

    def _analyze_service(self, key: str, rows: list[EventLog]) -> dict[str, Any]:
        n = len(rows)
        n_timeout = 0
        n_failure = 0
        n_slow = 0
        durations = []
        affected_cases: set[int] = set()
        affected_users: Counter[str] = Counter()
        error_messages: Counter[str] = Counter()
        remote_system = None

        for r in rows:
            meta = r.metadata_json or {}
            if meta.get("timeout") is True:
                n_timeout += 1
            if meta.get("success") is False:
                n_failure += 1
            d_ms = meta.get("total_time_ms")
            if isinstance(d_ms, (int, float)):
                durations.append(d_ms)
                if d_ms > self.slow_threshold_ms:
                    n_slow += 1
            if meta.get("error_message"):
                error_messages[str(meta["error_message"])] += 1
            if meta.get("remote_system") and remote_system is None:
                remote_system = meta["remote_system"]
            if r.case_id is not None:
                affected_cases.add(r.case_id)
            if r.actor_id:
                affected_users[r.actor_id] += 1

        failure_rate_pct = (n_failure / n * 100.0) if n else 0.0

        result: dict[str, Any] = {
            "service":             key,
            "remote_system":       remote_system,
            "record_count":        n,
            "timeout_count":       n_timeout,
            "failure_count":       n_failure,
            "failure_rate_pct":    round(failure_rate_pct, 1),
            "slow_call_count":     n_slow,
            "avg_duration_ms":     round(mean(durations), 1) if durations else None,
            "max_duration_ms":     max(durations) if durations else None,
            "affected_case_count": len(affected_cases),
            "affected_user_count": len(affected_users),
            "top_affected_users":  dict(affected_users.most_common(5)),
            "top_errors":          dict(error_messages.most_common(3)),
            "severity":            "normal",
            "observation":         None,
        }

        if n_timeout >= self.timeout_critical:
            result["severity"] = "critical"
            result["observation"] = (
                f"{n_timeout} timeouts on service {key} "
                f"(threshold {self.timeout_critical}) across {len(affected_cases)} case(s)"
            )
        elif failure_rate_pct >= self.failure_high_rate:
            result["severity"] = "high"
            result["observation"] = (
                f"{n_failure}/{n} calls failed on {key} ({failure_rate_pct:.0f}%)"
            )
        elif n_slow >= self.slow_high_count:
            result["severity"] = "high"
            result["observation"] = (
                f"{n_slow} slow calls (>{int(self.slow_threshold_ms)}ms) on {key}"
            )
        else:
            result["observation"] = f"No critical pattern on service {key}"

        return result
