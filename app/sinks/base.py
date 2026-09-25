from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Sequence

from app.db.models import EventLog


class BaseSink(ABC):
    """Consumes the output of an analyzer for one source_type.

    Subclasses receive:
      - source_type:    the YAML key for this source
      - rows:           the EVENT_LOG rows the analyzer was run against
      - result:         whatever the analyzer returned (Phase 2: list[Finding])
      - analyzer_class: fully qualified class name of the analyzer (for audit /
                        persistence). Optional kwarg so existing handlers
                        that don't care can ignore it.
    """

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    @abstractmethod
    def handle(
        self,
        source_type: str,
        rows: Sequence[EventLog],
        result: Any,
        analyzer_class: str | None = None,
    ) -> None:
        ...
