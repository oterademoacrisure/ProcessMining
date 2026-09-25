# Mule Logs Integration (Task #14) — Developer Documentation

**Status:** implemented & verified end-to-end against live Postgres.
**Scope:** integrate the standalone `mule-rca` module into serverops so Mulesoft
runtime logs become a first-class telemetry source whose evidence is correlated
with the existing Appian sources during root-cause analysis.

---

## 1. What this delivers

Before this work, serverops ingested three Appian-ecosystem sources plus infra
sources (Prometheus/Fluentd). They could detect *that* an integration was
unhealthy (timeouts in `integration_trace`, paused processes, task errors) but
not *where* the failure physically lived.

Mule sits in the call path between Appian and external systems, so its runtime
logs are ground truth:

- A `SocketTimeoutException` from Mule's HTTP connector = the **downstream
  service** did not respond.
- A `flow.error` before any outbound `http.request` = **Mule itself** failed.

This integration adds Mule as a 4th source and lets the RCA investigator cite
that deterministic localization, collapsing a *probable* cause into a
*definitive* one ("page the downstream owner; do not retry Appian/Mule").

---

## 2. Design decisions

| Decision | Choice | Why |
|---|---|---|
| Persistence | **Drop DuckDB entirely** | serverops is Postgres + SQLAlchemy + Alembic with one shared `event_log` table. The standalone module's DuckDB layer is dead weight here. |
| Where Mule fits | **Tier-1 source** (reader + analyzer + sink), wired via `config/modules.yaml` | Mirrors the 3 Appian sources exactly; the orchestrator never changes. |
| Cross-source correlation | **Handled by the existing pipeline** — no new "enrich incident" step | Mule events share a `case_id` with Appian events, so the existing incident grouper merges them. |
| Blast radius | **Fit existing contracts** | No change to the grouper, Incident model, investigator, or DB schema. |
| Reused from `mule-rca` | `MuleIntegrationEvent` schema + the outcome-classification logic (`group_into_executions` / `_classify_outcome` / `build_summary`) | The genuinely valuable logic. |
| NOT reused | `enrich_incident_with_mule`, DuckDB DDL, `register_with` | Built for assumptions serverops doesn't share. |

The standalone `mule-rca/` directory is **left untouched**; serverops got its
own copy of the reusable core under `app/mule/`.

---

## 3. The correlation mechanism (the linchpin)

The entire integration hinges on one fact: **a Mule event and the Appian
integration call it served must resolve to the same numeric `case_id`.**

```
Mule event:    appian_process_id = "78812"
                        │  (MuleReader maps it to...)
                        ▼
EventLogPayload: case_reference_id = "78812",  process_definition_name = "appian_default"
                        │  (persist_events resolves the natural key...)
                        ▼
event_log row:  case_id = 1014   ◄── SAME id the Appian integration_trace row gets,
                                     because AppianIntegrationTraceReader maps its
                                     "Process ID" the same way, under the same
                                     process_definition_name.
```

Downstream, everything is automatic:

1. `MuleAnalyzer` emits a `Finding` whose `contributing_rows` carry that `case_id`.
2. `FindingsEmitterSink` stamps `finding.case_ids` from those rows.
3. `incident_grouper` unions any findings sharing a `case_id` (or `server_id`) +
   overlapping time → the Mule finding and Appian finding land in **one incident**.
4. `LlmRcaInvestigator` runs once per incident, so its prompt contains both the
   Appian symptom and Mule's deterministic localization.

> **Critical config invariant:** the `mule` source's `process_definition_name`
> MUST equal the `appian_integration_trace` source's (`appian_default`). If they
> differ, the natural keys resolve to different `case_id`s and nothing merges.

---

## 4. Files changed

### New files

| File | Purpose |
|---|---|
| `app/mule/__init__.py` | Package marker / overview. |
| `app/mule/schemas.py` | `MuleIntegrationEvent` (Pydantic v2 wire schema). Ported from `mule-rca`. |
| `app/mule/correlation.py` | Outcome classification: `group_into_executions`, `_classify_outcome`, `build_summary`, value objects, `DOWNSTREAM_FAULT`. Ported (DuckDB + `enrich_incident_with_mule` dropped). |
| `app/readers/mule_reader.py` | `MuleReader(BaseReader)` — reads JSONL, validates, quarantines bad rows, yields `EventLogPayload` with the `appian_process_id → case_reference_id` mapping. |
| `app/analyzers/mule_analyzer.py` | `MuleAnalyzer(BaseAnalyzer)` — rehydrates events, classifies via the ported classification core, emits one `Finding` per implicated downstream system. |
| `scripts/demo_full.py` | One-shot deterministic end-to-end demo (reset → ingest → dispatch → investigate, prints each stage). |
| `data/input/mule/mule_integration.jsonl` | Sample Mule data (copied from `mule-rca/data/`). |
| `docs/MULE_INTEGRATION.md` | This document. |

### Changed files

| File | Change |
|---|---|
| `config/modules.yaml` | (a) Added the `mule:` source block (reader + analyzer + Console/FindingsEmitter sinks, `process_definition_name: appian_default`). (b) Added `{ source_type: mule, min_severity: high }` to the `llm_rca` investigator triggers. (c) Set `servicenow_mode: mock` under `llm_rca` config (test-environment convenience; see Section 7). |

### Deliberately unchanged (proof the integration "fits existing contracts")

`app/orchestrator/*` (dispatcher, registry, incident_grouper, investigator_dispatcher),
`app/investigators/*`, `app/db/models.py`, `app/readers/persistence.py`,
`app/readers/base.py`, `app/sinks/findings_emitter_sink.py` — **no changes**.
`pillars.py` already mapped `mule → pillar_2_bpm`, so even that needed nothing.

---

## 5. How the outcome -> severity mapping works

`MuleAnalyzer` groups failed flow executions by the system they implicate, then:

| Failed executions for a subject | Severity |
|---|---|
| `>= critical_failure_count` (default 3) | `critical` |
| `>= high_failure_count` (default 1) | `high` |
| else | `normal` (dropped by FindingsEmitterSink) |

Subjects: `mule_internal_error` → `mule_runtime`; downstream faults → the
`downstream_system` name. Each Finding carries `cited_event_ids` (the
http.error/flow.error event_ids) for the investigator to quote.

Thresholds are overridable per-source via `config:` in `modules.yaml`.

---

## 6. How to run / test

### Prerequisites
- Use the **Windows venv**: `serverops\venv\Scripts\python.exe`.
  (The `serverops\servops` venv is a Linux/uv venv — unusable on Windows.)
- `serverops\.env` must have `DATABASE_URL` (Postgres). Azure OpenAI creds are
  optional — without them the investigator produces evidence-only reports
  (the merge still works, just no LLM narrative).
- Schema migrated: `python -m alembic upgrade head` (already at `0009`).
- Sample data present at `data/input/mule/mule_integration.jsonl`.

### Option A — one-shot deterministic demo (recommended)

```powershell
# from repo root
serverops\venv\Scripts\python.exe serverops\scripts\demo_full.py --focus
```

Flags:
- `--focus` — ingest only `appian_integration_trace` + `appian_task_errors` +
  `mule`, producing a clean downstream-localized incident (clearest demo of
  Mule's value).
- *(no flag)* — ingest all real sources (richer, more realistic; the added infra
  evidence makes the LLM hedge between "downstream slow" vs "webapp exhausted").
- `--include-simulator` — also ingest the synthetic simulator source.
- `--keep` — append to existing data instead of resetting first.
- `--keep-servicenow` — do NOT strip the ServiceNow sink (will emit noise; see Section 7).

The script prints all five stages and flags the merged report with `<== MERGED`.

### Option B — the real runner

```powershell
cd serverops
venv\Scripts\python.exe app\dev_reset.py        # clean slate (also clears reader state)
venv\Scripts\python.exe app\runner.py --tenant 1 --interval 5 --duration 30
```
Note: multi-cycle runner runs can group findings slightly differently depending
on which cycle each finding is polled in (see Section 7). For a deterministic result use
Option A.

### Option C — unit tests for the ported classification logic

```powershell
cd mule-rca
.venv\Scripts\python.exe -m pytest tests/     # 18 tests over schema/reader/grouping
```

---

## 7. Stage-by-stage validation (SQL)

Run after `demo_full.py --focus`. Each query confirms one stage.

**Stage 1a — events landed:**
```sql
SELECT source_type, count(*) FROM event_log GROUP BY source_type ORDER BY 1;
-- PASS: mule=89, appian_integration_trace=20, appian_task_errors=12
```

**Stage 1b — Mule & Appian share a case_id (THE linchpin):**
```sql
SELECT case_id,
       count(*) FILTER (WHERE source_type = 'mule')       AS mule_events,
       count(*) FILTER (WHERE source_type LIKE 'appian%') AS appian_events
FROM event_log
WHERE case_id IS NOT NULL
GROUP BY case_id
HAVING count(*) FILTER (WHERE source_type = 'mule') > 0
   AND count(*) FILTER (WHERE source_type LIKE 'appian%') > 0
ORDER BY case_id;
-- PASS: rows where BOTH counts > 0 (e.g. case 77: mule=4, appian=1)
-- FAIL (empty): process_definition_name mismatch between mule and appian sources
```

**Stage 1c — the mapping is correct:**
```sql
SELECT e.case_id, pc.case_reference_id,
       e.metadata_json->>'appian_process_id' AS mule_process_id
FROM event_log e JOIN process_case pc ON pc.case_id = e.case_id
WHERE e.source_type = 'mule' AND e.metadata_json->>'appian_process_id' IS NOT NULL
LIMIT 5;
-- PASS: case_reference_id == mule_process_id
```

**Stage 2 — findings produced, carrying case_ids:**
```sql
SELECT source_type, severity, subject_key, observation, case_ids->'values' AS case_ids
FROM finding ORDER BY source_type, severity DESC;
-- PASS: a 'mule' finding per failed downstream (e.g. credit-bureau-prod, critical),
--       observation localizes to the downstream; case_ids overlap an appian finding's.
```

**Stage 3/4 — the merge + explanation (master check):**
```sql
SELECT report_id, severity,
       payload->'incident_sources'                AS sources,
       payload->'incident_finding_count'          AS findings_in_incident,
       payload->'primary_trigger'->>'source_type' AS primary,
       summary
FROM root_cause_report ORDER BY produced_at DESC;
-- PASS: a report whose sources include BOTH 'mule' and an 'appian_*' with
--       findings_in_incident > 1, summary naming the downstream system.
-- Note: a separate ['mule']-only report is EXPECTED for any Mule finding whose
--       case_id no Appian finding referenced (correct, not a bug).
```

**Stage 5 — remediation actions:**
```sql
SELECT action_type, state, action_text, command
FROM remediation_action
WHERE report_id = (SELECT max(report_id) FROM root_cause_report)
ORDER BY action_id;
-- PASS: actions exist, ideally downstream-focused.
```

**One-shot end-to-end proof:**
```sql
SELECT report_id, payload->'incident_sources' AS sources, summary
FROM root_cause_report
WHERE payload->'incident_sources' @> '"mule"' AND payload::text LIKE '%appian%'
ORDER BY produced_at DESC LIMIT 1;
-- A returned row means ingest → flag → merge → explain all worked for Mule.
```

### Reference: a verified passing run

- `event_log`: mule=89, appian_integration_trace=20, appian_task_errors=12.
- Findings: 3 mule (credit-bureau-prod/critical, docstore-prod/high,
  customer-service-prod/high), 2 appian_integration_trace, 1 appian_task_errors.
- Reports: one merged `['appian_integration_trace','appian_task_errors','mule']`
  (5 findings, primary=mule, summary localizing to the downstream services), plus
  one `['mule']`-only report for the customer-service-prod finding (case had no
  Appian counterpart).

---

## 8. Known limitations & call-outs

1. **Single-pillar confidence cap.** `mule` and `appian_*` are both
   `pillar_2_bpm`. The investigator caps confidence when only one pillar is
   present, so even a clean Mule+Appian incident stays mildly hedged. To remove
   the hedge, add cross-pillar infra confirmation (Prometheus/Fluentd showing the
   downstream host down) or give Mule its own pillar — both deliberate, out of scope.
2. **Cross-poll flow splitting.** `group_into_executions` assumes a flow's ~4
   events (one `correlation_id`) arrive in one poll batch. True for the sample;
   at scale a flow could split across polls. Would need execution-state tracking.
3. **Multi-cycle grouping variance.** The live runner can place findings in
   different incidents depending on which cycle polls them. The demo script's
   single-pass design avoids this.
4. **Pre-existing ServiceNow bugs (NOT Mule-related).** Real mode fails DNS on the
   placeholder instance URL; mock mode hits `StringDataRightTruncation` because the
   mock ticket number exceeds the `servicenow_number varchar(64)` column. Both are
   non-fatal (sinks are exception-isolated). `demo_full.py` strips the ServiceNow
   sink at runtime to keep output clean; `servicenow_mode: mock` was set in
   `modules.yaml` as a convenience. Fix separately if mock ServiceNow is needed.

---

## 9. The 8 integration points (from `mule-rca/docs/integration.md`), resolved

| # | Original seam | Resolution |
|---|---|---|
| 1 | Schema strictness | Kept `extra="ignore"`; bad rows quarantined. |
| 2 | Registry attr names | Use `source_type` class attr; dropped `source_name`/`target_table`/`schema_class`. |
| 3 | DDL auto-create | Dropped — Alembic + shared `event_log`. |
| 4 | tenant_id semantics | Stamp the reader's int `tenant_id`. |
| 5 | Single-row INSERT | Dropped — runner batches via `persist_events`. |
| 6 | `register_with()` | Dropped — wired via `modules.yaml`. |
| 7 | `enrich_incident_with_mule` | Not used; logic repackaged into `MuleAnalyzer`; merge via shared `case_id`. |
| 8 | Demo stubs | N/A in production; `scripts/demo_full.py` is the serverops demo. |
