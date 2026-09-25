from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable


@dataclass
class EventLogPayload:
    tenant_id: int
    source_type: str
    timestamp: datetime
    activity_name: str | None = None
    activity_type: str | None = None
    lifecycle_stage: str | None = None
    actor_id: str | None = None
    system_id: str | None = None
    server_id: str | None = None
    sequence_number: int | None = None
    duration: float | None = None
    case_id: int | None = None
    process_id: int | None = None
    metadata_json: dict | None = field(default=None)

    # Natural-key fields. When set, persist_events resolves them to the
    # numeric process_id / case_id FKs (lookup-or-create). Infra sources
    # leave these None and supply nothing case-related.
    process_definition_name: str | None = None
    case_reference_id: str | None = None


class BaseReader(ABC):
    source_type: str = ""

    def __init__(self, tenant_id: int, config: dict | None = None):
        if not self.source_type:
            raise ValueError(f"{type(self).__name__} must define a class-level `source_type`")
        self.tenant_id = tenant_id
        self.config = config or {}

    @abstractmethod
    def read(self) -> Iterable[EventLogPayload]:
        """Pull from the source and yield normalized EventLogPayload instances.

        Implementations track their own ingestion state (file offsets,
        last-read timestamps, etc.). State is advanced only when the runner
        calls `commit()` after successful persistence — so a crash between
        yield and persist causes re-emission rather than data loss.
        """
        ...

    def commit(self) -> None:
        """Advance reader state after the runner has persisted what `read()` yielded.

        Default is a no-op. Stateful readers override to record what's now safe
        to skip on the next poll.
        """
        return None
