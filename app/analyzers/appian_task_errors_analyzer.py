from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from app.analyzers.base import BaseAnalyzer
from app.db.models import EventLog
from app.findings import Finding


DEFAULTS = {
    "user_burst_high_count": 2,         # >= N errors from one user in the batch => high
    "total_burst_critical_count": 8,    # >= N total errors in the batch => critical (mass-user impact)
}


class AppianTaskErrorsAnalyzer(BaseAnalyzer):
    """Detects bursts of task-access errors.

    Two angles:
      - Per-user burst: one user hitting multiple broken tasks (their
        personal workspace is degraded).
      - Cross-user burst: many users hitting errors at once (system-wide
        symptom — often follows a process-engine cascade).
    """

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        merged = {**DEFAULTS, **(self.config or {})}
        self.user_burst_high = int(merged["user_burst_high_count"])
        self.total_burst_critical = int(merged["total_burst_critical_count"])

    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        if not rows:
            return []

        per_user: Counter[str] = Counter()
        details_counter: Counter[str] = Counter()
        timestamps = []
        for r in rows:
            if r.actor_id:
                per_user[r.actor_id] += 1
            meta = r.metadata_json or {}
            details_counter[meta.get("details") or "unknown"] += 1
            if r.timestamp:
                timestamps.append(r.timestamp)

        total = len(rows)
        bursting_users = {u: c for u, c in per_user.items() if c >= self.user_burst_high}

        severity = "normal"
        observations = []

        if total >= self.total_burst_critical:
            severity = "critical"
            observations.append(
                f"{total} task-access errors in this batch across {len(per_user)} user(s) "
                f"(threshold {self.total_burst_critical}) - likely system-wide impact"
            )
        elif bursting_users:
            severity = "high"
            top = ", ".join(f"{u}={c}" for u, c in sorted(bursting_users.items(), key=lambda x: -x[1])[:3])
            observations.append(f"User burst: {top}")
        else:
            observations.append("No burst pattern detected")

        payload = {
            "record_count":        total,
            "unique_users":        len(per_user),
            "bursting_users":      bursting_users,
            "details_breakdown":   dict(details_counter),
            "time_min":            min(timestamps).isoformat() if timestamps else None,
            "time_max":            max(timestamps).isoformat() if timestamps else None,
            "severity":            severity,
            "observation":         "; ".join(observations),
        }
        return [Finding(
            severity=severity,
            subject_key="appian_task_errors",
            observation=payload["observation"],
            payload=payload,
            contributing_rows=list(rows),
        )]
