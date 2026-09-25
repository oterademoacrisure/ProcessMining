from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EventLog, ProcessCase, ProcessDefinition
from app.readers.base import EventLogPayload
from app.observability import progress  # POINT 21 (Task #21): live stage events


_NATURAL_KEY_FIELDS = ("process_definition_name", "case_reference_id")


def persist_events(session: Session, payloads: Iterable[EventLogPayload]) -> int:
    """Persist a batch of EventLogPayloads, resolving natural keys to FKs.

    For payloads that carry `process_definition_name` and/or `case_reference_id`,
    the matching ProcessDefinition / ProcessCase row is looked up (created on
    first sight). The numeric FKs (`process_id`, `case_id`) are populated on
    the EventLog row before insert.

    All work happens in a single transaction.
    """
    payloads = list(payloads)
    if not payloads:
        return 0

    process_def_map = _resolve_process_definitions(session, payloads)
    case_map = _resolve_cases(session, payloads, process_def_map)

    rows = []
    for p in payloads:
        kwargs = asdict(p)
        for k in _NATURAL_KEY_FIELDS:
            kwargs.pop(k, None)

        resolved_process_id = None
        if p.process_definition_name:
            resolved_process_id = process_def_map.get(
                (p.tenant_id, p.process_definition_name)
            )

        resolved_case_id = None
        if p.case_reference_id and resolved_process_id is not None:
            resolved_case_id = case_map.get(
                (p.tenant_id, resolved_process_id, p.case_reference_id)
            )

        kwargs["process_id"] = resolved_process_id or p.process_id
        kwargs["case_id"] = resolved_case_id or p.case_id

        rows.append(EventLog(**kwargs))

    session.add_all(rows)
    session.commit()

    # POINT 21 (Task #21): INGEST telemetry — global "system activity" feed (this
    # happens before any incident exists, so it's keyed by tenant, not correlation).
    srcs = sorted({getattr(p, "source_type", None) for p in payloads if getattr(p, "source_type", None)})
    progress.emit("INGEST", "succeeded", tenant_id=payloads[0].tenant_id,
                  step=f"Collected {len(rows)} live telemetry signal(s) from {', '.join(srcs) or 'monitored systems'}",
                  meta={"count": len(rows), "sources": srcs})
    return len(rows)


def _resolve_process_definitions(
    session: Session, payloads: list[EventLogPayload]
) -> dict[tuple[int, str], int]:
    keys = {
        (p.tenant_id, p.process_definition_name)
        for p in payloads
        if p.process_definition_name
    }
    out: dict[tuple[int, str], int] = {}
    for tenant_id, name in keys:
        out[(tenant_id, name)] = _get_or_create_process_definition(
            session, tenant_id, name
        )
    return out


def _resolve_cases(
    session: Session,
    payloads: list[EventLogPayload],
    process_def_map: dict[tuple[int, str], int],
) -> dict[tuple[int, int, str], int]:
    keys: set[tuple[int, int, str]] = set()
    for p in payloads:
        if not (p.case_reference_id and p.process_definition_name):
            continue
        pid = process_def_map.get((p.tenant_id, p.process_definition_name))
        if pid is None:
            continue
        keys.add((p.tenant_id, pid, p.case_reference_id))
    out: dict[tuple[int, int, str], int] = {}
    for tenant_id, process_id, case_ref in keys:
        out[(tenant_id, process_id, case_ref)] = _get_or_create_case(
            session, tenant_id, process_id, case_ref
        )
    return out


def _get_or_create_process_definition(
    session: Session, tenant_id: int, name: str
) -> int:
    existing = session.scalar(
        select(ProcessDefinition.process_id)
        .where(ProcessDefinition.tenant_id == tenant_id)
        .where(ProcessDefinition.process_name == name)
        .limit(1)
    )
    if existing is not None:
        return existing
    new_row = ProcessDefinition(tenant_id=tenant_id, process_name=name)
    session.add(new_row)
    session.flush()
    return new_row.process_id


def _get_or_create_case(
    session: Session, tenant_id: int, process_id: int, case_ref: str
) -> int:
    existing = session.scalar(
        select(ProcessCase.case_id)
        .where(ProcessCase.tenant_id == tenant_id)
        .where(ProcessCase.process_id == process_id)
        .where(ProcessCase.case_reference_id == case_ref)
        .limit(1)
    )
    if existing is not None:
        return existing
    new_row = ProcessCase(
        tenant_id=tenant_id,
        process_id=process_id,
        case_reference_id=case_ref,
        status="Open",
    )
    session.add(new_row)
    session.flush()
    return new_row.case_id
