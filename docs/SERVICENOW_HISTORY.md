# ServiceNow Historical Precedent (Task #19) — Developer Documentation

**Status:** implemented & verified end-to-end against a live ServiceNow dev
instance + Postgres.
**Scope:** give the RCA investigator *memory* — when a new incident is
investigated, find similar PAST ServiceNow incidents and their resolutions and
feed them into the root-cause analysis ("we've seen this before — here's how it
was fixed").

> Every change for this task is tagged `# POINT 19:` in the source. Run
> `grep -rn "POINT 19" serverops` to see everything it touched.

---

## 1. What this delivers

serverops already WRITES ServiceNow tickets from RCA reports
(`app/sinks/servicenow_sink.py`). Task #19 adds the REVERSE direction: it READS
resolved ServiceNow incidents and uses them as precedent during RCA. This closes
the operational loop:

```
logs -> correlate -> RCA report -> [WRITE] ServiceNow ticket
                                        -> human resolves it (adds the fix)
   next incident <- [ENRICH] RCA cites the past fix <- [RETRIEVE] <- [PULL] resolved tickets
```

The investigator's summary now reads e.g. *"...consistent with INC0009999;
recommend paging the downstream vendor as in that prior resolution."*

---

## 2. Design decisions

| Decision | Choice | Why |
|---|---|---|
| Where history lives | A dedicated `historical_incident` table, queried at RCA time | Past resolved cases are not live telemetry; keeping them out of `event_log`/`finding` avoids polluting the live grouper/investigator. |
| Similarity method | **Postgres full-text search** (tsvector/ts_rank) + structural match | `pgvector` is not installed/available on this Postgres, and tickets share concrete vocabulary (system names, error types). |
| Embeddings | Deferred, but **embedding-ready** | A `rerank` seam in retrieval lets an embedding/LLM reranker drop in as "Stage 2" later with no other changes. |
| Incremental watermark | **Derived from the data** — `MAX(sys_updated_on)` per tenant | No separate JSON/state file to lose in ephemeral containers; impossible to desync from the data. |
| Recency | Guard (only precedents resolved before the incident) + gentle tiebreaker (newer ranks higher) | A fix can't be precedent for an earlier incident; fresher fixes are more trustworthy. |
| Safety | Fail-safe + config toggle | Empty history / retrieval error / disabled -> no precedent block, RCA behaves exactly as before. |
| Sync cadence | A standalone script (run on a schedule), not in the fast pipeline loop | ServiceNow history changes slowly; no need to poll it every cycle. |

---

## 3. Files

### New
| File | Purpose |
|---|---|
| `alembic/versions/0010_historical_incident.py` | Migration: the `historical_incident` table + generated `search_tsv` full-text column + indexes (incl. the watermark index). |
| `app/db/models.py` (`HistoricalIncident`) | ORM model for the table. `search_tsv` is `Computed(persisted=True)` (read-only, DB-generated). |
| `app/servicenow_history/client.py` | Pulls resolved incidents from the ServiceNow REST API (paginated, `display_value=all` for readable names, incremental via `since`). |
| `app/servicenow_history/loader.py` | Idempotent upsert into the table (ON CONFLICT on `(tenant_id, sys_id)`). |
| `app/servicenow_history/retrieval.py` | `find_precedents(...)` — scores past incidents (recurrence + system + text + recency), returns top-k. |
| `scripts/sync_servicenow_history.py` | CLI: backfill / incremental sync (owns the DB-derived watermark). |
| `tests/test_servicenow_history.py` | Unit tests for the pure logic (no DB/network). |
| `docs/SERVICENOW_HISTORY.md` | This document. |

### Changed (additive)
| File | Change |
|---|---|
| `app/investigators/llm_rca_investigator.py` | New `_fetch_precedents()` step + a "HISTORICAL PRECEDENT" prompt block + precedents stored in the report payload. Exception-isolated and config-toggled. |
| `app/ui/streamlit_app.py` | A "Similar past incidents" panel on the report view (reads the saved precedents). |
| `config/modules.yaml` | `history_enabled / history_k / history_min_score` under the `llm_rca` investigator config. |
| `.env.example` | Note that `SERVICENOW_*` are now used for reading too (needs read access to the incident table). |

### Unchanged (proof it fits existing contracts)
The pipeline (readers/dispatcher/grouper), the Mule work, the DB models for the
live tables, and the existing sinks are untouched.

---

## 4. How retrieval scores "similar" (retrieval.py)

For a live incident, `find_precedents` scores each past incident:

Each matched signal is independent evidence of relevance, with a confidence it
ALONE implies. They combine via **noisy-OR** into a 0..1 confidence:

```
confidence = 1 - PRODUCT(1 - p_signal)

p = 0.95   exact recurrence (same correlation_id)
    0.70   same system (same cmdb_ci)
    0.30   same category
    <=0.60 text overlap (scaled ts_rank, TEXT_SCALE knob)

examples:  recurrence ~0.95 | same-system ~0.70 | same-system+text ~0.85
           strong-text-only ~0.60 | category-only ~0.30
```
**Recency (0..3) is NOT part of confidence** — it only breaks ties in ordering.
This is deliberate: otherwise a merely-recent but irrelevant ticket would look
confident. Confidence measures *relevance only*.

Guards: tenant-scoped; only incidents resolved at/before the incident time
(`as_of`); must hit at least one real signal; **confidence >= `history_min_confidence`
(default 0.60)**. If nothing clears the bar, NO precedent is shown — better than a
misleading one. 0.60 ~= "at least a same-system-level match."

Verified live: a seeded credit-bureau precedent reached confidence **~0.95**
(recurrence + system) and was kept; unrelated tickets fell below 0.60 and were
dropped (no precedent shown for an unrelated incident).

---

## 5. How to run

### Prerequisites
- Windows venv: `serverops\venv\Scripts\python.exe`.
- `.env` has `DATABASE_URL` and `SERVICENOW_INSTANCE_URL/USERNAME/PASSWORD`
  (read access to the `incident` table, incl. resolution notes).
- Schema migrated: `python -m alembic upgrade head` (>= revision `0010`).

### Sync ServiceNow history
```powershell
# first run = full backfill; later runs = incremental (watermark)
serverops\venv\Scripts\python.exe serverops\scripts\sync_servicenow_history.py
serverops\venv\Scripts\python.exe serverops\scripts\sync_servicenow_history.py --full   # force full
serverops\venv\Scripts\python.exe serverops\scripts\sync_servicenow_history.py --tenant 2
```
Schedule it (Task Scheduler / cron) for ongoing freshness — it's idempotent.

### Tune / toggle (config/modules.yaml, under `llm_rca`)
```yaml
history_enabled: true     # false = feature off, RCA behaves as before
history_k: 3              # how many precedents to surface
history_min_score: 2.0   # raise to cut weak text-only matches
```

### See it
RCA runs automatically pick up precedents. View them in Streamlit
(`Root-Cause Reports` -> expand a report -> "Similar past incidents").

---

## 6. How to test

### Unit tests (pure logic, no DB/ServiceNow)
```powershell
# pytest is not in the venv by default:
serverops\venv\Scripts\python.exe -m pip install pytest
cd serverops
venv\Scripts\python.exe -m pytest tests/test_servicenow_history.py -q
```
Covers: text->tsquery tokenizing, ServiceNow record normalization (reference
resolution), truncation/date-parsing, and the correlation_id recipe.

### Live validation (SQL)
```sql
-- history loaded?
SELECT count(*), max(sys_updated_on) FROM historical_incident WHERE tenant_id = 1;

-- did a report pick up precedents?
SELECT report_id,
       payload->'historical_precedent_count' AS n,
       payload->'historical_precedents'      AS precedents,
       summary
FROM root_cause_report ORDER BY produced_at DESC LIMIT 3;
```
PASS = a report whose `historical_precedents` contains a relevant ticket, and
whose `summary` cites it.

---

## 7. Known limitations & call-outs

1. **Dev instance has only generic demo tickets** (USB/SAN/eFax), not our domain.
   Relevant precedent appears once our own tickets are resolved there, or you
   seed domain incidents. The plumbing is independent of data quality.
2. **No embeddings yet.** Full-text + structural match only. The `rerank` seam in
   `retrieval.py` is where embedding/LLM reranking drops in later (needs Azure
   embedding access or pgvector).
3. **pytest not in the venv / no package index here** — install pytest where you
   have access; tests are standard pytest style.
4. **LLM citation is best-effort.** The prompt now *requires* citing a matching
   precedent's ticket number; the model usually complies, but it's probabilistic.
   The precedent is always retrieved, attached to the report, and shown in the UI
   regardless.
5. **Migration lesson:** migration `0010` was edited after first being applied,
   which broke its `downgrade` (an index that didn't exist yet). In a shared/prod
   environment, add a NEW migration instead of editing an applied one. (Here it
   was dev-only and recovered cleanly.)

---

## 8. The full cycle, in one line

```
[WRITE] RCA -> ServiceNow ticket   (servicenow_sink.py)
        -> operator resolves it (adds the fix)
[PULL]  sync_servicenow_history.py -> historical_incident table
[RETRIEVE] retrieval.find_precedents() -> top-k similar past incidents
[ENRICH] llm_rca_investigator -> precedents in the prompt + on the report
[SHOW]  streamlit_app.py -> "Similar past incidents" panel
```
