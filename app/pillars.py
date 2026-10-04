"""Source-type → pillar mapping.

Three product pillars define what kind of log a source produces:

  pillar_1_desktop  — User-level desktop activity capture
                      (Soroco Scout, ActivTrak, MS Activity Insights, ...)
  pillar_2_bpm      — Business process / integration / workflow telemetry
                      (Appian, Mule, Pega, Camunda, ...)
  pillar_3_infra    — Infrastructure / application runtime telemetry
                      (Prometheus, Fluentd, Datadog, Splunk, ...)

Used by the LLM investigator to compute a "data coverage" block telling
the LLM which pillars are represented in a given incident window (so the
LLM can honestly lower its confidence when coverage is partial).
"""
from __future__ import annotations

from typing import Iterable


PILLAR_1_DESKTOP = "pillar_1_desktop"
PILLAR_2_BPM     = "pillar_2_bpm"
PILLAR_3_INFRA   = "pillar_3_infra"
PILLAR_UNKNOWN   = "pillar_unknown"


# Explicit mapping. Sources not listed fall back to PILLAR_UNKNOWN.
SOURCE_TO_PILLAR: dict[str, str] = {
    # Pillar 1 — desktop / user-task capture
    "soroco_scout":              PILLAR_1_DESKTOP,
    "activtrak":                 PILLAR_1_DESKTOP,
    "ms_activity_insights":      PILLAR_1_DESKTOP,

    # Pillar 2 — BPM / workflow / integration
    "appian_process_metrics":    PILLAR_2_BPM,
    "appian_integration_trace":  PILLAR_2_BPM,
    "appian_task_errors":        PILLAR_2_BPM,
    "appian_audit_logins":       PILLAR_2_BPM,
    "appian_sail_trace":         PILLAR_2_BPM,
    "mule":                      PILLAR_2_BPM,
    "mule_application":          PILLAR_2_BPM,
    "pega":                      PILLAR_2_BPM,
    "camunda":                   PILLAR_2_BPM,
    "workato":                   PILLAR_2_BPM,

    # Pillar 3 — infra / application runtime
    "prometheus":                PILLAR_3_INFRA,
    "fluentd":                   PILLAR_3_INFRA,
    "kubernetes_pod_logs":       PILLAR_3_INFRA,  # K8S-POD-LOGS
    "azure_container_logs":      PILLAR_3_INFRA,  # AZURE-MONITOR
    "datadog":                   PILLAR_3_INFRA,
    "splunk":                    PILLAR_3_INFRA,
    "loki":                      PILLAR_3_INFRA,
    "elasticsearch":             PILLAR_3_INFRA,
    "simulator":                 PILLAR_3_INFRA,
}


ALL_PILLARS = (PILLAR_1_DESKTOP, PILLAR_2_BPM, PILLAR_3_INFRA)


PILLAR_DESCRIPTIONS = {
    PILLAR_1_DESKTOP: "user/desktop activity",
    PILLAR_2_BPM:     "business process / integration",
    PILLAR_3_INFRA:   "infrastructure / runtime",
    PILLAR_UNKNOWN:   "uncategorized source",
}


def pillar_of(source_type: str) -> str:
    return SOURCE_TO_PILLAR.get(source_type, PILLAR_UNKNOWN)


def pillars_present(source_types: Iterable[str]) -> list[str]:
    """Return sorted unique pillars represented by the given source_types."""
    out = {pillar_of(st) for st in source_types}
    out.discard(PILLAR_UNKNOWN)
    return sorted(out)


def pillars_absent(source_types: Iterable[str]) -> list[str]:
    present = set(pillars_present(source_types))
    return [p for p in ALL_PILLARS if p not in present]