from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,          # POINT 19: declares a DB-generated (read-only) column
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Index,
    UniqueConstraint,  # POINT 19: dedup key for historical_incident
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR  # POINT 19: TSVECTOR = full-text search type
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Tenant(Base):
    __tablename__ = "tenant"

    tenant_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    industry: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")

    process_definitions: Mapped[list["ProcessDefinition"]] = relationship(back_populates="tenant")
    cases: Mapped[list["ProcessCase"]] = relationship(back_populates="tenant")
    events: Mapped[list["EventLog"]] = relationship(back_populates="tenant")


class ProcessDefinition(Base):
    __tablename__ = "process_definition"

    process_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    process_name: Mapped[str] = mapped_column(String(255), nullable=False)
    process_version: Mapped[str] = mapped_column(String(64), nullable=False, default="1")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="process_definitions")
    cases: Mapped[list["ProcessCase"]] = relationship(back_populates="process")


class ProcessCase(Base):
    __tablename__ = "process_case"

    case_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    process_id: Mapped[int] = mapped_column(ForeignKey("process_definition.process_id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False, index=True)
    case_reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    case_owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    case_metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    process: Mapped["ProcessDefinition"] = relationship(back_populates="cases")
    tenant: Mapped["Tenant"] = relationship(back_populates="cases")
    events: Mapped[list["EventLog"]] = relationship(back_populates="case")


class EventLog(Base):
    __tablename__ = "event_log"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("process_case.case_id", ondelete="SET NULL"), nullable=True)
    process_id: Mapped[int | None] = mapped_column(ForeignKey("process_definition.process_id", ondelete="SET NULL"), nullable=True)

    source_type: Mapped[str] = mapped_column(String(64), nullable=False)

    activity_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    activity_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lifecycle_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    system_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    server_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sequence_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    tenant: Mapped["Tenant"] = relationship(back_populates="events")
    case: Mapped["ProcessCase | None"] = relationship(back_populates="events")

    __table_args__ = (
        Index("ix_event_log_tenant_unprocessed", "tenant_id", "processed_at"),
        Index("ix_event_log_tenant_source", "tenant_id", "source_type"),
        Index("ix_event_log_timestamp", "timestamp"),
        Index("ix_event_log_case", "case_id"),
    )


class Finding(Base):
    __tablename__ = "finding"

    finding_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer_class: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    subject_key: Mapped[str] = mapped_column(String(255), nullable=False)
    observation: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Correlation keys, flattened for fast filtering + a JSONB blob for richer ones.
    server_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    case_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    actor_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    contributing_event_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    time_min: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    time_max: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    correlation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_finding_tenant_unprocessed", "tenant_id", "processed_at"),
        Index("ix_finding_tenant_severity_time", "tenant_id", "severity", "produced_at"),
        Index("ix_finding_hash_time", "correlation_hash", "produced_at"),
        Index("ix_finding_source_type", "source_type"),
    )


class RootCauseReport(Base):
    __tablename__ = "root_cause_report"

    report_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False)
    investigator_class: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_finding_id: Mapped[int | None] = mapped_column(
        ForeignKey("finding.finding_id", ondelete="SET NULL"), nullable=True
    )
    trigger_finding_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_chain: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    related_event_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    related_finding_ids: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    correlation_keys: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    produced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    servicenow_number: Mapped[str | None] = mapped_column(String(64), nullable=True)
    servicenow_sys_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    servicenow_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    __table_args__ = (
        Index("ix_root_cause_report_tenant_time", "tenant_id", "produced_at"),
        Index("ix_root_cause_report_tenant_severity", "tenant_id", "severity"),
        Index("ix_root_cause_report_trigger", "trigger_finding_id"),
    )


class RemediationAction(Base):
    __tablename__ = "remediation_action"

    action_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False,
    )
    report_id: Mapped[int] = mapped_column(
        ForeignKey("root_cause_report.report_id", ondelete="CASCADE"), nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    action_text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")

    # Classification: "executable" = goes through LangGraph remediation workflow.
    #                 "advisory"   = manual step; never executes; UI tracks Done/Skip only.
    action_type: Mapped[str] = mapped_column(String(16), nullable=False, default="advisory")
    command: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_steps: Mapped[str | None] = mapped_column(Text, nullable=True)

    approver_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Phase D — execution outcome
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verify_result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # POINT 20 (Task #20): Jira ticket key raised for this command execution (via MCP).
    jira_key: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        Index("ix_remediation_action_report",       "report_id"),
        Index("ix_remediation_action_tenant_state", "tenant_id", "state"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# POINT 19 (Task #19): ServiceNow historical incidents as an RCA precedent store
# ─────────────────────────────────────────────────────────────────────────────
class HistoricalIncident(Base):
    """POINT 19: One PAST (resolved/closed) ServiceNow incident + its resolution.

    Populated by app/servicenow_history/loader.py from the ServiceNow REST API.
    Read by the RCA investigator (via app/servicenow_history/retrieval.py) to
    surface "we've seen this before — here's how it was fixed" precedent.

    Kept deliberately separate from the live pipeline tables (event_log /
    finding / root_cause_report): these are historical reference rows, not live
    telemetry, and must never feed the live grouper/investigator as new events.
    """

    __tablename__ = "historical_incident"

    incident_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False
    )

    # Identity / dedup
    number: Mapped[str | None] = mapped_column(String(64), nullable=True)      # human ticket id, e.g. INC0010013
    sys_id: Mapped[str | None] = mapped_column(String(64), nullable=True)      # POINT 19: ServiceNow unique id — dedup key

    # Text (these three feed the generated search_tsv below)
    short_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    close_notes: Mapped[str | None] = mapped_column(Text, nullable=True)       # POINT 19: the resolution — the precedent payload

    # Structural-match fields
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    subcategory: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cmdb_ci: Mapped[str | None] = mapped_column(String(255), nullable=True)    # POINT 19: affected system, stored as a resolved NAME
    priority: Mapped[str | None] = mapped_column(String(16), nullable=True)
    state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # POINT 19: did the fix work? approved | rejected | unknown. Sourced from the
    # ServiceNow close_code (Solved* -> approved, Not Solved* -> rejected) or an
    # operator HITL decision. Precedent retrieval labels/prefers proven fixes and
    # warns against ones that were tried and failed.
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, server_default="unknown")

    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sys_updated_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # POINT 19: source last-update; MAX per tenant = sync watermark

    correlation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)  # POINT 19: recurrence-match key
    source_system: Mapped[str] = mapped_column(String(32), nullable=False, default="servicenow")
    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)        # POINT 19: full original ticket (future-proofing)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # POINT 19: DB-generated full-text column — the database fills it from the
    # three text fields above. `Computed(..., persisted=True)` marks it STORED
    # and READ-ONLY, so the ORM never tries to insert/update it (it would error
    # otherwise). Only the retrieval layer reads it. The expression MUST match
    # the one in migration 0010.
    search_tsv: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', "
            "coalesce(short_description, '') || ' ' || "
            "coalesce(description, '') || ' ' || "
            "coalesce(close_notes, ''))",
            persisted=True,
        ),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_historical_incident_search_tsv", "search_tsv", postgresql_using="gin"),
        Index("ix_historical_incident_tenant", "tenant_id"),
        Index("ix_historical_incident_correlation", "correlation_id"),
        Index("ix_historical_incident_cmdb_ci", "cmdb_ci"),
        Index("ix_historical_incident_tenant_updated", "tenant_id", "sys_updated_on"),
        UniqueConstraint("tenant_id", "sys_id", name="uq_historical_incident_tenant_sysid"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# POINT 21 (Task #21): live pipeline-stage telemetry — the feed behind the
# real-time "what's happening now" timeline in the UI. Append-only: one row per
# stage boundary / sub-step. PURE telemetry — never read by the pipeline itself,
# so writing it can never change behaviour (and a write failure is swallowed).
# ─────────────────────────────────────────────────────────────────────────────
class PipelineEvent(Base):
    __tablename__ = "pipeline_event"

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(
        ForeignKey("tenant.tenant_id", ondelete="CASCADE"), nullable=False
    )
    # Grouping keys:
    #   correlation_id — ties all events of ONE incident into a single timeline
    #                    (set from CORRELATE onward, once an incident exists).
    #   run_id         — ties pre-incident events (ingest/detect) to a runner cycle.
    correlation_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    stage: Mapped[str] = mapped_column(String(32), nullable=False)          # INGEST … LEARN
    step: Mapped[str | None] = mapped_column(String(255), nullable=True)    # optional sub-step label
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="started")  # started|progress|succeeded|failed|skipped
    level: Mapped[str] = mapped_column(String(16), nullable=False, default="info")       # info|warn|error
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)         # inc_number, jira_key, confidence, counts, duration_ms…

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        # per-incident timeline lookup (tenant + correlation, ordered by event_id)
        Index("ix_pipeline_event_tenant_corr", "tenant_id", "correlation_id", "event_id"),
        # global "recent activity" feed (tenant, newest first)
        Index("ix_pipeline_event_tenant_created", "tenant_id", "created_at"),
    )
