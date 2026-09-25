from __future__ import annotations

from abc import ABC, abstractmethod

from sqlalchemy.orm import Session

from app.findings import RootCauseReport
from app.orchestrator.incident_grouper import Incident


class BaseInvestigator(ABC):
    """Tier-2 cross-source analyzer.

    Triggered by an Incident (one or more correlated tier-1 findings the
    dispatcher grouped together); queries EVENT_LOG and the Finding table
    for related evidence on matching correlation keys (server_id + time
    window, case_id, actor_id, …) across the source_types it's interested
    in. Produces a `RootCauseReport` synthesizing the multi-source story.

    Subclasses declare:
      - `name` (class attribute)
      - which source_types + severities trigger them (declared in YAML
        `triggers:`, not on the class)

    Subclasses implement:
      - `investigate(incident, session)` returning a RootCauseReport
        (or None if no story emerged).
    """

    name: str = ""

    def __init__(self, config: dict | None = None):
        if not self.name:
            raise ValueError(f"{type(self).__name__} must define a class-level `name`")
        self.config = config or {}

    @abstractmethod
    def investigate(self, incident: Incident, session: Session) -> RootCauseReport | None:
        ...
