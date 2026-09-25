from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from app.db.models import EventLog
from app.findings import Finding


class BaseAnalyzer(ABC):
    """Per-source pattern analyzer.

    The dispatcher groups EVENT_LOG rows by `source_type`, looks up the matching
    analyzer via the registry, and hands the rows to `analyze()`. Each analyzer
    is responsible for interpreting `metadata_json` according to its own
    source's schema and emitting one or more `Finding` per subject group.

    Analyzers must populate `Finding.contributing_rows` with the exact rows
    they used to reach each conclusion — the downstream FindingsEmitterSink
    uses those rows to compute correlation keys (server_id, case_id, etc.)
    that the investigator will later query on.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def analyze(self, rows: Sequence[EventLog]) -> list[Finding]:
        ...
