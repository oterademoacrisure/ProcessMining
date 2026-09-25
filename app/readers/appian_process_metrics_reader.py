from __future__ import annotations

from datetime import datetime

from app.readers.appian_csv_reader import AppianCsvReader
from app.readers.base import EventLogPayload


_INT_COLUMNS = (
    "Total Processes",
    "Active Processes",
    "Completed Processes",
    "Paused Processes",
    "Paused by Exception Processes",
    "Cancelled Processes",
    "Unfinished Processes",
    "Terminating Processes",
    "Total Nodes",
    "Completed Nodes",
    "Waiting Nodes",
    "In-Process Nodes",
    "Paused Nodes",
    "Paused By Exception Nodes",
)

_KEY_MAP = {
    "Total Processes":               "total_processes",
    "Active Processes":              "active_processes",
    "Completed Processes":           "completed_processes",
    "Paused Processes":              "paused_processes",
    "Paused by Exception Processes": "paused_by_exception_processes",
    "Cancelled Processes":           "cancelled_processes",
    "Unfinished Processes":          "unfinished_processes",
    "Terminating Processes":         "terminating_processes",
    "Total Nodes":                   "total_nodes",
    "Completed Nodes":               "completed_nodes",
    "Waiting Nodes":                 "waiting_nodes",
    "In-Process Nodes":              "in_process_nodes",
    "Paused Nodes":                  "paused_nodes",
    "Paused By Exception Nodes":     "paused_by_exception_nodes",
}


class AppianProcessMetricsReader(AppianCsvReader):
    """Reads Appian's aggregate process metrics log (process.csv).

    This is a gauge log: one row per scrape, system-wide counts. No case or
    actor information — only `timestamp` and the configured `server_id`
    (typically the service-manager pod hostname) are present, with all
    counts in `metadata_json`.
    """

    source_type = "appian_process_metrics"
    log_filename_glob = "process.csv"

    def to_payload(self, row: dict, timestamp: datetime) -> EventLogPayload:
        metrics: dict[str, int | None] = {}
        for col in _INT_COLUMNS:
            raw = row.get(col)
            try:
                metrics[_KEY_MAP[col]] = int(raw) if raw not in (None, "") else None
            except (TypeError, ValueError):
                metrics[_KEY_MAP[col]] = None

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=timestamp,
            activity_name="process_metrics_snapshot",
            activity_type="automated",
            lifecycle_stage="complete",
            system_id="appian",
            server_id=None,
            metadata_json=metrics,
        )
