from __future__ import annotations

import json
import logging
import os
import re  # POINT 19
from datetime import timedelta
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import EventLog, Finding as FindingRow
from app.findings import RootCauseReport
from app.investigators.base import BaseInvestigator
from app.orchestrator.incident_grouper import Incident
from app.pillars import (
    ALL_PILLARS,
    PILLAR_DESCRIPTIONS,
    pillar_of,
    pillars_absent,
    pillars_present,
)
from app.utils.llm_handler import clean_llm_output
from app.servicenow_history.retrieval import find_precedents, Precedent  # POINT 19 (Task #19)
from app.observability import progress  # POINT 21 (Task #21): live stage events


log = logging.getLogger(__name__)


# Per-source fields to drop from sample event metadata so the prompt stays compact.
DROP_METADATA_FIELDS = {
    "raw",                   # prometheus normalizer keeps the entire original record
    "sample_errors",         # fluentd analyzer pre-aggregates these; redundant for raw events
    "top_error_loggers",
    "details_breakdown",
}

# Verbose payload fields to drop when summarizing findings.
DROP_PAYLOAD_FIELDS = {
    "sample_errors",
    "raw",
}


class LlmRcaInvestigator(BaseInvestigator):
    """Gathers correlated evidence and asks an LLM to produce a root-cause
    hypothesis WITHOUT presuming any specific cause type.

    Receives an `Incident` (one or more tier-1 findings the dispatcher
    grouped together by shared correlation keys + time window). The LLM
    sees ALL findings in the incident plus a sample of EVENT_LOG rows on
    the incident's combined correlation keys. One LLM call per incident,
    not per finding.

    If no Azure OpenAI credentials are configured, falls back to an
    evidence-only report (no LLM synthesis).
    """

    name = "llm_rca"

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        load_dotenv()

        self.window_before_min = int(self.config.get("window_minutes_before", 5))
        self.window_after_min = int(self.config.get("window_minutes_after", 1))
        self.max_related_findings = int(self.config.get("max_related_findings", 20))
        self.max_sample_events_per_source = int(self.config.get("max_sample_events_per_source", 5))
        self.temperature = float(self.config.get("llm_temperature", 0.2))
        # POINT 19 (Task #19): historical ServiceNow precedent retrieval.
        self.history_enabled = bool(self.config.get("history_enabled", True))
        self.history_k = int(self.config.get("history_k", 3))
        self.history_min_confidence = float(self.config.get("history_min_confidence", 0.60))
        # POINT 19: vector store + embedder for the semantic recall arm (hybrid
        # retrieval). Built from config (vector_backend default faiss). Fail-safe:
        # if construction fails, retrieval falls back to keyword + structural.
        self._vector_store = None
        self._embedder = None
        self._last_usage: dict | None = None   # POINT 21: token usage of the last LLM call
        self._last_model: str | None = None     # POINT 21: real model id from the response (for Langfuse cost)
        if self.history_enabled:
            try:
                from app.vectorstore import Embedder, get_vector_store
                self._vector_store = get_vector_store(self.config)
                self._embedder = Embedder(self.config)
            except Exception:
                log.exception(
                    "LlmRcaInvestigator: vector store/embedder init failed; "
                    "precedent retrieval will use keyword+structural only"
                )
        self.deployment = self.config.get("llm_deployment") or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        self._sample_source_types_override: list[str] | None = (
            list(self.config["sample_source_types"])
            if "sample_source_types" in self.config else None
        )

        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if not api_key or not endpoint or not self.deployment:
            self._client = None
            log.warning("LlmRcaInvestigator: Azure OpenAI not configured; falling back to evidence-only reports")
        else:
            # Azure v1 API surface: the OpenAI(base_url=...) client must point at
            # <resource>/openai/v1. Normalize so a bare resource root (or a stray env
            # override that dropped the path) still works — mirrors the embedder's
            # endpoint handling and stops the recurring 404 on /chat/completions.
            base = endpoint.rstrip("/")
            if not base.endswith("/openai/v1"):
                from urllib.parse import urlparse
                p = urlparse(endpoint)
                base = f"{p.scheme}://{p.netloc}/openai/v1"
            self._client = OpenAI(api_key=api_key, base_url=base)

    def investigate(self, incident: Incident, session: Session) -> RootCauseReport | None:
        if not incident.findings:
            return None

        # Anchor on the latest time across the incident (so post-incident
        # events fall within the +N-min lookahead).
        time_anchor = incident.time_max or incident.time_min
        if time_anchor is None:
            return None
        window_start = (incident.time_min or time_anchor) - timedelta(minutes=self.window_before_min)
        window_end = time_anchor + timedelta(minutes=self.window_after_min)

        primary = incident.primary_finding
        incident_finding_ids = {f.finding_id for f in incident.findings}

        # POINT 21 (Task #21): live timeline — DIAGNOSE stage. Keyed by the SAME
        # correlation slug the ServiceNow sink builds, so the whole incident reads
        # as one thread (diagnose -> ticket -> remediate -> learn).
        _corr = _precedent_correlation_id(primary)
        _tenant = primary.tenant_id
        # POINT 21 (Task #21): which log sources make up this incident (e.g.
        # prometheus, mule, appian_integration_trace) — shown on the timeline.
        _sources = sorted({f.source_type for f in incident.findings})
        progress.emit("DIAGNOSE", "started", tenant_id=_tenant, correlation_id=_corr,
                      step="Analyzing the incident across affected systems",
                      message=(primary.observation or primary.subject_key),
                      meta={"sources": _sources, "trigger": primary.source_type})

        related_findings = self._fetch_related_findings(
            session, primary.tenant_id, incident_finding_ids, window_start, window_end,
        )
        sample_events = self._fetch_sample_events(
            session, primary.tenant_id, incident, window_start, window_end,
        )
        coverage = self._build_coverage(incident, related_findings, sample_events)

        # POINT 19: retrieve similar PAST ServiceNow incidents (precedent) to
        # ground the RCA. Fail-safe: errors/empty -> no precedent block.
        progress.emit("DIAGNOSE", "progress", tenant_id=_tenant, correlation_id=_corr,
                      step="Searching our incident history for similar past cases")
        precedents = self._fetch_precedents(incident, session)
        _prec_msg = (
            f"Found {len(precedents)} proven historical resolution(s) to learn from"
            if precedents else
            "No close historical match — diagnosing from live evidence"
        )
        progress.emit("DIAGNOSE", "progress", tenant_id=_tenant, correlation_id=_corr,
                      step=_prec_msg, meta={"precedents": len(precedents)})

        prompt = self._build_prompt(incident, related_findings, sample_events, coverage, precedents)
        progress.emit("DIAGNOSE", "progress", tenant_id=_tenant, correlation_id=_corr,
                      step="Combining live evidence with proven history to find the cause")
        llm_output = self._call_llm(prompt) if self._client else None
        # POINT 21: record the LLM call as a model event (-> Langfuse/OTel when enabled).
        # Prefer the real model id from the response (Langfuse cost mapping); fall back
        # to the Azure deployment name.
        progress.generation(tenant_id=_tenant, correlation_id=_corr,
                            model=(self._last_model or self.deployment), usage=self._last_usage)

        if llm_output and isinstance(llm_output, dict):
            summary = llm_output.get("summary") or primary.observation or "(no summary from LLM)"
            severity = self._map_severity(llm_output.get("severity"), primary.severity)
            evidence_chain = llm_output.get("evidence_chain") or self._fallback_evidence_chain(incident, related_findings)
            llm_status = "ok"
        else:
            summary = f"(LLM unavailable; evidence-only) {primary.observation or primary.subject_key}"
            severity = primary.severity
            evidence_chain = self._fallback_evidence_chain(incident, related_findings)
            llm_status = "fallback"

        _done_msg = (
            f"Root cause identified — recommendation backed by {len(precedents)} proven past fix(es)"
            if precedents else
            "Root cause identified from the live evidence"
        )
        progress.emit("DIAGNOSE", "succeeded", tenant_id=_tenant, correlation_id=_corr,
                      step=_done_msg,
                      meta={"severity": severity, "llm_status": llm_status,
                            "precedents": len(precedents)})

        return RootCauseReport(
            investigator_class=type(self).__name__,
            trigger_finding_id=primary.finding_id,
            trigger_finding_ids=sorted(incident_finding_ids),
            severity=severity,
            summary=summary,
            evidence_chain=evidence_chain,
            related_event_ids=[e.event_id for e in sample_events],
            related_finding_ids=[f.finding_id for f in related_findings],
            correlation_keys={
                "tenant_id":   primary.tenant_id,
                "time_anchor": time_anchor.isoformat(),
                "window":      [window_start.isoformat(), window_end.isoformat()],
                "infra_hosts": incident.all_server_ids,
                "case_ids":    incident.all_case_ids,
                "incident_size": len(incident.findings),
            },
            payload={
                "primary_trigger": {
                    "source_type": primary.source_type,
                    "subject":     primary.subject_key,
                    "observation": primary.observation,
                },
                "incident_finding_count": len(incident.findings),
                "incident_sources":       sorted({f.source_type for f in incident.findings}),
                "related_finding_count":  len(related_findings),
                "sample_event_count":     len(sample_events),
                "coverage":               coverage,
                "llm_status":             llm_status,
                # POINT 21 (Task #21): persist the timeline key so the remediation
                # stages later thread their events onto this SAME incident timeline.
                "timeline_correlation_id": _corr,
                "llm_raw":                llm_output,
                # POINT 19: precedents shown in the UI + saved for the record.
                "historical_precedents":      [_precedent_to_dict(p) for p in precedents],
                "historical_precedent_count": len(precedents),
            },
        )

    # --- evidence gathering ---------------------------------------------

    def _fetch_related_findings(
        self,
        session: Session,
        tenant_id: int,
        incident_finding_ids: set[int],
        window_start,
        window_end,
    ) -> list[FindingRow]:
        if not incident_finding_ids:
            incident_finding_ids = {-1}
        stmt = (
            select(FindingRow)
            .where(FindingRow.tenant_id == tenant_id)
            .where(FindingRow.finding_id.notin_(incident_finding_ids))
            .where(FindingRow.severity.in_(["high", "critical"]))
            .where(FindingRow.time_max >= window_start)
            .where(FindingRow.time_min <= window_end)
            .order_by(FindingRow.time_min)
            .limit(self.max_related_findings)
        )
        return list(session.scalars(stmt).all())

    def _fetch_sample_events(
        self,
        session: Session,
        tenant_id: int,
        incident: Incident,
        window_start,
        window_end,
    ) -> list[EventLog]:
        infra_hosts = incident.all_server_ids
        case_ids = incident.all_case_ids
        if not infra_hosts and not case_ids:
            return []

        scope = []
        if infra_hosts:
            scope.append(EventLog.server_id.in_(infra_hosts))
        if case_ids:
            scope.append(EventLog.case_id.in_(case_ids))

        source_types = self._sample_source_types_override or self._discover_source_types(
            session, tenant_id, window_start, window_end,
        )

        sample: list[EventLog] = []
        for st in source_types:
            stmt = (
                select(EventLog)
                .where(EventLog.tenant_id == tenant_id)
                .where(EventLog.source_type == st)
                .where(EventLog.timestamp >= window_start)
                .where(EventLog.timestamp <= window_end)
                .where(or_(*scope))
                .order_by(EventLog.timestamp)
                .limit(self.max_sample_events_per_source)
            )
            sample.extend(session.scalars(stmt).all())
        return sample

    def _discover_source_types(self, session, tenant_id, window_start, window_end) -> list[str]:
        stmt = (
            select(EventLog.source_type)
            .where(EventLog.tenant_id == tenant_id)
            .where(EventLog.timestamp >= window_start)
            .where(EventLog.timestamp <= window_end)
            .distinct()
        )
        return list(session.scalars(stmt).all())

    # POINT 19 (Task #19): historical-precedent retrieval -------------------
    def _fetch_precedents(self, incident: Incident, session: Session) -> list[Precedent]:
        """Find similar PAST ServiceNow incidents for this incident.

        Fail-safe and toggleable: if disabled, on error, or with no match, it
        returns [] and the RCA proceeds exactly as before (no precedent block).
        """
        if not self.history_enabled:
            return []
        try:
            primary = incident.primary_finding
            # systems = infra hosts + the systems the findings name (a Mule
            # finding's subject IS the downstream system, e.g. credit-bureau-prod).
            systems = sorted(
                {s for s in (incident.all_server_ids or []) if s}
                | {f.subject_key for f in incident.findings if f.subject_key}
            )
            observations = " ".join(
                f.observation for f in incident.findings if f.observation
            )
            return find_precedents(
                session,
                primary.tenant_id,
                correlation_id=_precedent_correlation_id(primary),
                systems=systems,
                query_text=observations or primary.observation,
                as_of=incident.time_max or incident.time_min,
                k=self.history_k,
                min_confidence=self.history_min_confidence,
                store=self._vector_store,      # POINT 19: semantic arm (None -> keyword+structural only)
                embedder=self._embedder,
            )
        except Exception:
            log.exception(
                "LlmRcaInvestigator: precedent lookup failed; continuing without precedent"
            )
            return []

    # --- prompt + LLM ---------------------------------------------------

    def _build_coverage(self, incident: Incident, related_findings, sample_events) -> dict:
        """Compute which product pillars are represented in this incident
        window. Used to tell the LLM what evidence is available so it can
        be honest about confidence when coverage is partial.
        """
        all_source_types: set[str] = set()
        density: dict[str, int] = {}

        def _bump(st: str) -> None:
            if not st:
                return
            density[st] = density.get(st, 0) + 1
            all_source_types.add(st)

        for f in incident.findings:
            _bump(f.source_type)
        for f in related_findings:
            _bump(f.source_type)
        for e in sample_events:
            _bump(e.source_type)

        present = pillars_present(all_source_types)
        absent  = pillars_absent(all_source_types)

        notes: list[str] = []
        for pillar in absent:
            notes.append(
                f"{pillar} ({PILLAR_DESCRIPTIONS[pillar]}) is ABSENT — "
                f"root causes residing in this pillar cannot be confirmed "
                f"or ruled out from the evidence in this window."
            )
        if len(present) == 1:
            notes.insert(
                0,
                f"Only one pillar is represented: {present[0]}. "
                "Cross-pillar correlation is not possible — "
                "the hypothesis must reflect this limitation.",
            )

        return {
            "pillars_present":  present,
            "pillars_absent":   absent,
            "source_density":   dict(sorted(density.items(), key=lambda kv: -kv[1])),
            "notes":            notes,
        }

    def _build_prompt(self, incident: Incident, related_findings, sample_events, coverage: dict, precedents=None) -> str:
        incident_block = {
            "finding_count":    len(incident.findings),
            "affected_servers": incident.all_server_ids,
            "affected_cases":   incident.all_case_ids,
            "time_window":      [
                incident.time_min.isoformat() if incident.time_min else None,
                incident.time_max.isoformat() if incident.time_max else None,
            ],
            "findings": [
                {
                    "source_type": f.source_type,
                    "subject":     f.subject_key,
                    "severity":    f.severity,
                    "observation": f.observation,
                    "time_window": [
                        f.time_min.isoformat() if f.time_min else None,
                        f.time_max.isoformat() if f.time_max else None,
                    ],
                    "details":     _trim_dict(f.payload, DROP_PAYLOAD_FIELDS),
                }
                for f in incident.findings
            ],
        }

        related_block = [
            {
                "source_type": f.source_type,
                "subject":     f.subject_key,
                "severity":    f.severity,
                "observation": f.observation,
                "time_window": [
                    f.time_min.isoformat() if f.time_min else None,
                    f.time_max.isoformat() if f.time_max else None,
                ],
                "summary":     _trim_dict(f.payload, DROP_PAYLOAD_FIELDS),
            }
            for f in related_findings
        ]

        sample_block = [
            {
                "source_type": e.source_type,
                "timestamp":   e.timestamp.isoformat() if e.timestamp else None,
                "server_id":   e.server_id,
                "system_id":   e.system_id,
                "actor_id":    e.actor_id,
                "metadata":    _trim_dict(e.metadata_json, DROP_METADATA_FIELDS),
            }
            for e in sample_events
        ]

        # POINT 19: build the precedent section (empty string if no precedents,
        # so the prompt is unchanged from today's behavior when none are found).
        precedents = precedents or []
        precedent_block = [
            {
                "ticket":      p.number,
                "system":      p.cmdb_ci,
                "what":        p.short_description,
                "resolution":  p.close_notes,
                "outcome":     p.outcome,                       # POINT 19: approved | rejected | unknown
                "confidence":  round(p.confidence, 2),
                "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
            }
            for p in precedents
        ]
        precedent_section = ""
        if precedent_block:
            precedent_section = (
                "\n=== HISTORICAL PRECEDENT (similar PAST incidents, their resolution, and whether the fix WORKED) ===\n"
                + json.dumps(precedent_block, indent=2, default=str)
                + "\n\nEach precedent has an `outcome` — use it carefully:\n"
                  "  - approved : the fix was applied and VERIFIED to work — a PROVEN fix. Prefer it; "
                  "base a `recommended_actions` item on its resolution.\n"
                  "  - rejected : the fix was TRIED and did NOT work — do NOT re-recommend it; "
                  "take its failure into account.\n"
                  "  - unknown  : outcome not recorded — treat as a hint, not proof.\n"
                  "If a precedent closely matches (same system and/or symptoms), you MUST name its ticket "
                  "number in your `summary` (e.g. \"consistent with INC0001234\"). "
                  "Only ignore precedents that are not genuinely related to this incident.\n"
            )

        return f"""You are an expert incident-response analyst. An incident has been detected — a group of findings that share correlation keys (host / case / time window) and likely describe ONE underlying problem viewed from multiple sources.

Your job: synthesize ONE root-cause hypothesis for this incident. Use ALL the findings together (not each one in isolation). Do NOT presume any specific cause type — synthesize from what the evidence actually shows. If the evidence is insufficient, say so plainly.

=== DATA COVERAGE ===
{json.dumps(coverage, indent=2, default=str)}

IMPORTANT: review the coverage block above carefully.
  - If a pillar is ABSENT, you MUST NOT confidently attribute root cause to that pillar's domain. State explicitly what evidence from the absent pillar would be needed.
  - If only ONE pillar is present, cap your confidence at 0.6 — single-pillar evidence is rarely sufficient to confidently identify a root cause.
  - If TWO pillars are present, you can reach up to ~0.85 confidence depending on evidence strength.
  - If ALL THREE pillars are present and converge, you may reach up to ~0.95 confidence.
  - Confidence above 0.95 should be reserved for cases where the evidence is unambiguous AND directly causal.

=== INCIDENT ({len(incident.findings)} finding(s) grouped together) ===
{json.dumps(incident_block, indent=2, default=str)}

=== OTHER RELATED FINDINGS IN WINDOW ({len(related_findings)}) ===
{json.dumps(related_block, indent=2, default=str)}

=== SAMPLE EVENTS IN WINDOW ({len(sample_events)}) ===
{json.dumps(sample_block, indent=2, default=str)}
{precedent_section}
=== RESPOND ===
Return ONLY valid JSON in this exact shape:
{{
  "summary": "<one or two sentences: the root-cause hypothesis for the incident as a whole>",
  "confidence": <0.0 to 1.0>,
  "severity": "<low|medium|high|critical>",
  "contributing_factors": ["<factor 1>", "<factor 2>", ...],
  "evidence_chain": [
    {{
      "signal":     "<short description of the signal>",
      "timestamp":  "<ISO timestamp or null>",
      "source":     "<source_type>",
      "role":       "<root|trail|symptom>"
    }}
  ],
  "recommended_actions": [
    {{
      "description":  "<plain-text description of the remediation step>",
      "type":         "executable" | "advisory",
      "command":      "<runnable shell command, REQUIRED when type=executable>",
      "manual_steps": "<step-by-step instructions, REQUIRED when type=advisory>"
    }},
    ...
  ],
  "uncertainties": ["<things the evidence does not tell you>", ...]
}}

CRITICAL — `recommended_actions` rules:

A) FORM
  - Each item is an OBJECT (not a string) with the exact 4 keys above.
  - For `type=executable`: `command` is required and `manual_steps` should be null.
  - For `type=advisory`:   `manual_steps` is required and `command` should be null.

B) CLASSIFICATION
  - `executable` = a remediation the system can run as a SINGLE shell command,
                   with no human follow-up needed. Restart, scale, kill, kubectl
                   rollout, systemctl, docker, etc. The command must be a SAFE,
                   self-contained one-liner — no pipes to destructive operations.
  - `advisory`   = a remediation requiring human judgement, config-file edits,
                   redeployment, multi-step procedures, or external coordination.

C) CONTENT
  - The investigation is ALREADY COMPLETE — this report IS the investigation.
  - DO NOT include descriptions starting with: "Investigate", "Analyze", "Analyse",
    "Review", "Identify", "Determine", "Examine", "Study", "Assess", "Evaluate".
  - DO NOT include passive "Monitor X" suggestions unless they specify a metric AND
    a threshold (e.g. "Add alerting when CPU > 90% for 3 consecutive minutes").
  - DO NOT restate evidence that's already in `evidence_chain`.
  - DO NOT use vague language ("address the issue", "fix the problem", "resolve").

GOOD examples:
  {{
    "description": "Restart the appian-webapp-03 pod to clear JVM heap pressure",
    "type":        "executable",
    "command":     "kubectl rollout restart deployment/appian-webapp",
    "manual_steps": null
  }}
  {{
    "description": "Increase JVM heap size on appian-webapp-03",
    "type":        "advisory",
    "command":     null,
    "manual_steps": "Edit deployment/appian-webapp; set JAVA_OPTS=-Xmx4g (currently -Xmx2g); apply and verify rollout"
  }}
  {{
    "description": "Scale appian-webapp deployment to absorb load spikes",
    "type":        "executable",
    "command":     "kubectl scale deployment/appian-webapp --replicas=5",
    "manual_steps": null
  }}
  {{
    "description": "Open a Jira ticket to track Hikari pool sizing review",
    "type":        "advisory",
    "command":     null,
    "manual_steps": "Open Jira PROJ-OPS ticket: 'Review Hikari pool size — observed exhaustion at 50 connections'. Assign to DB ops team."
  }}

If you have nothing concrete to recommend, return an empty list. An empty list is
better than a list of generic platitudes.

No markdown. No prose. Only JSON.
"""

    def _call_llm(self, prompt: str) -> dict | None:
        try:
            response = self._client.chat.completions.create(
                model=self.deployment,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert incident-response analyst. Reply only with JSON. "
                            "Make no claim that the evidence does not support."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=self.temperature,
            )
            raw = response.choices[0].message.content or ""
            # POINT 21 (Task #21): the REAL model id from the response (e.g.
            # gpt-4o-2024-08-06) — Langfuse recognises this for accurate cost,
            # unlike the Azure deployment alias.
            self._last_model = getattr(response, "model", None)
            # POINT 21 (Task #21): stash token usage for the model-event record.
            try:
                u = getattr(response, "usage", None)
                self._last_usage = {
                    "input": getattr(u, "prompt_tokens", None),
                    "output": getattr(u, "completion_tokens", None),
                    "total": getattr(u, "total_tokens", None),
                } if u else None
            except Exception:
                self._last_usage = None
            cleaned = clean_llm_output(raw)
            return json.loads(cleaned)
        except Exception:
            log.exception("LlmRcaInvestigator: LLM call failed")
            return None

    # --- helpers --------------------------------------------------------

    def _map_severity(self, llm_severity, fallback: str) -> str:
        if not llm_severity:
            return fallback
        s = str(llm_severity).strip().lower()
        if s in ("critical", "high"):
            return s
        if s == "medium":
            return "high"
        if s == "low":
            return "normal"
        return fallback

    def _fallback_evidence_chain(self, incident: Incident, related_findings) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        for f in incident.findings:
            chain.append({
                "signal":    f.observation,
                "timestamp": f.time_max.isoformat() if f.time_max else None,
                "source":    f.source_type,
                "role":      "incident_finding",
            })
        for f in related_findings:
            chain.append({
                "signal":    f.observation,
                "timestamp": f.time_min.isoformat() if f.time_min else None,
                "source":    f.source_type,
                "role":      "context",
            })
        return chain


def _trim_dict(d, drop_keys: set[str]) -> Any:
    if not isinstance(d, dict):
        return d
    return {k: v for k, v in d.items() if k not in drop_keys}


# POINT 19 (Task #19): precedent helpers --------------------------------------
def _precedent_correlation_id(finding) -> str:
    """Rebuild the SAME correlation_id the ServiceNow sink assigns, so a past
    ticket our own pipeline filed for this exact problem is caught as a
    recurrence (the strongest precedent signal)."""
    def slug(v: Any) -> str:
        s = re.sub(r"[^a-z0-9]+", "-", str(v or "").lower()).strip("-")
        return s[:60] if s else "unknown"
    return (
        f"rca-t{finding.tenant_id}-{slug(finding.source_type)}-"
        f"{slug(finding.subject_key)}-{slug(finding.severity)}"
    )[:100]


def _precedent_to_dict(p: Precedent) -> dict[str, Any]:
    """Serialize a Precedent for the report payload / UI."""
    return {
        "ticket":      p.number,
        "system":      p.cmdb_ci,
        "what":        p.short_description,
        "resolution":  p.close_notes,
        "outcome":     p.outcome,                  # POINT 19: approved | rejected | unknown
        "resolved_at": p.resolved_at.isoformat() if p.resolved_at else None,
        "confidence":  round(p.confidence, 2),
    }
