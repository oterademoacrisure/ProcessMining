from __future__ import annotations

from datetime import datetime

from app.readers.appian_csv_reader import AppianCsvReader
from app.readers.base import EventLogPayload


class AppianTaskErrorsReader(AppianCsvReader):
    """Reads Appian's task access errors log (task_errors.csv).

    Captures user attempts to open invalid/missing tasks. No Process ID is
    carried by this log (Gap-1 in the design); only `actor_id` (User UUID)
    + timestamp are available for cross-source correlation.
    """

    source_type = "appian_task_errors"
    log_filename_glob = "task_errors.csv"

    def to_payload(self, row: dict, timestamp: datetime) -> EventLogPayload:
        details = row.get("Details") or ""

        return EventLogPayload(
            tenant_id=self.tenant_id,
            source_type=self.source_type,
            timestamp=timestamp,
            activity_name="task_access_error",
            activity_type="manual",
            lifecycle_stage="complete",
            actor_id=(row.get("User") or None),
            system_id="appian",
            server_id=None,
            metadata_json={"details": details},
        )
