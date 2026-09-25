from __future__ import annotations

import logging
from typing import Sequence

from sqlalchemy.orm import Session

from app.db.models import EventLog
from app.orchestrator.db_reader import poll_unprocessed, mark_processed
from app.orchestrator.registry import Registry
from app.observability import progress  # POINT 21 (Task #21): live stage events


log = logging.getLogger(__name__)


class Dispatcher:
    """Polls EVENT_LOG via db_reader, groups rows by source_type, and routes each
    group through its registered analyzer + sinks. Rows are marked processed
    after the analyzer/sink pass completes — so a crash leaves them for retry."""

    def __init__(
        self,
        registry: Registry,
        tenant_ids: Sequence[int],
        batch_size: int = 500,
    ):
        self.registry = registry
        self.tenant_ids = list(tenant_ids)
        self.batch_size = batch_size

    def run_once(self, session: Session) -> int:
        rows = poll_unprocessed(session, self.tenant_ids, self.batch_size)
        if not rows:
            return 0

        groups: dict[str, list[EventLog]] = {}
        for r in rows:
            groups.setdefault(r.source_type, []).append(r)

        for source_type, batch in groups.items():
            self._handle_group(source_type, batch)

        mark_processed(session, rows)
        # POINT 21 (Task #21): DETECT telemetry — events analyzed into findings.
        progress.emit("DETECT", "succeeded", tenant_id=rows[0].tenant_id,
                      step=f"Analyzed {len(rows)} signal(s) and flagged anomalies",
                      meta={"count": len(rows)})
        return len(rows)

    def _handle_group(self, source_type: str, rows: list[EventLog]) -> None:
        try:
            self.registry.get(source_type)
        except KeyError:
            log.warning(
                "No module registered for source_type=%r; %d row(s) will be marked processed without analysis",
                source_type, len(rows),
            )
            return

        try:
            analyzer = self.registry.build_analyzer(source_type)
            result = analyzer.analyze(rows)
        except Exception:
            log.exception("Analyzer failed for source_type=%r", source_type)
            return

        analyzer_class = f"{type(analyzer).__module__}.{type(analyzer).__name__}"

        for sink in self.registry.build_sinks(source_type):
            try:
                sink.handle(
                    source_type=source_type,
                    rows=rows,
                    result=result,
                    analyzer_class=analyzer_class,
                )
            except Exception:
                log.exception(
                    "Sink %s failed for source_type=%r",
                    type(sink).__name__, source_type,
                )
