# Current State & Function Call Flow

> One-shot map of the ServerOps process-mining / RCA / remediation platform as it exists today
> (single-process, single-container, shared Postgres). Written to feed the **APIFICATION** effort
> that splits Readers and UI out into their own deployables.

---

## 1. Two entry points

| Entry point                                | Role                              |
|--------------------------------------------|-----------------------------------|
| [app/runner.py](../app/runner.py)          | Backend loop (ingest → dispatch → investigate → remediate) |
| [app/ui/streamlit_app.py](../app/ui/streamlit_app.py) | Streamlit dashboard — read-only viewer + operator decisions on remediation actions |

Both entry points share **one Postgres database** (see [app/db/session.py](../app/db/session.py)) and **one YAML config** ([config/modules.yaml](../config/modules.yaml)). There is no in-process communication between them today; they coordinate purely through DB tables and the filesystem.

---

## 2. Function-call flow — the `runner.py` cycle

```
app/runner.py :: run(config, tenant_id, interval_sec, duration_sec)
├── Registry.load(config/modules.yaml)                                   [app/orchestrator/registry.py:83]
│   └── returns Registry(modules, investigators, remediation, observability)
├── Dispatcher(registry, tenant_ids=[tenant_id])                         [app/orchestrator/dispatcher.py]
├── InvestigatorDispatcher(registry, tenant_ids=[tenant_id])             [app/orchestrator/investigator_dispatcher.py]
├── RemediationDispatcher(config=remediation_cfg, tenant_ids=[tenant_id])[app/orchestrator/remediation_dispatcher.py]
├── progress.configure(obs_cfg)   +  init_telemetry(obs_cfg)             [app/observability/*]
│
└── while not _stop:  ─────────────────────────────────────────────────  the pipeline cycle
    ├── _ingest_cycle(registry, tenant_id)
    │   for source_type in registry.source_types():
    │       reader = registry.build_reader(source_type, tenant_id)      # instantiates concrete Reader
    │       payloads = list(reader.read())                                # source-specific pull
    │       with SessionLocal() as s: n = persist_events(s, payloads)   # [app/readers/persistence.py]
    │       reader.commit()                                              # advance checkpoint (JSON on disk)
    │   (persist_events emits INGEST telemetry to pipeline_event)
    │
    ├── _dispatch_cycle(dispatcher) → dispatcher.run_once(session)
    │   ├─ poll_unprocessed(session, tenant_ids) → up to 500 event_log rows  [orchestrator/db_reader.py]
    │   ├─ group rows by source_type
    │   ├─ for each group: analyzer = Registry.build_analyzer(...)
    │   │                  result   = analyzer.analyze(rows)
    │   │                  for sink in Registry.build_sinks(...): sink.handle(result)
    │   │                    (findings_emitter_sink writes to `finding` table)
    │   └─ mark rows processed_at = now()
    │   (emits DETECT telemetry)
    │
    ├── _investigate_cycle(inv_dispatcher) → inv_dispatcher.run_once(session)
    │   ├─ poll_unprocessed_findings(session, tenant_ids)                [orchestrator/findings_reader.py]
    │   ├─ group_findings_into_incidents(findings)                       [orchestrator/incident_grouper.py]
    │   │    (union-find by server_id/case_id + time overlap)
    │   ├─ for each incident:
    │   │    investigators = registry.investigators_for(source_type, severity)
    │   │    for inv_entry in investigators:
    │   │        report = investigator.investigate(incident)             # LLM RCA
    │   │        for sink in inv_entry.sink_classes(): sink.handle(report)
    │   │          (writes root_cause_report, remediation_action rows; posts ServiceNow)
    │   └─ mark findings processed
    │   (emits CORRELATE telemetry)
    │
    └── _remediation_cycle(rem_dispatcher) → rem_dispatcher.run_once(session)
        └─ for each RemediationAction where state='approved' and action_type='executable':
            run_workflow(action_id, config)                              [app/remediation/langgraph_workflow.py]
              VALIDATE → EXECUTE → VERIFY → REPORT
            (updates remediation_action.state, files Jira ticket, posts SNOW comment)
    then sleep(interval_sec) and loop.
```

**Key invariant** — the cycle is idempotent per row (each of `event_log`, `finding`, `remediation_action` has a `processed_at` / `state` gate). A crash between `reader.read()` and `persist_events` re-emits on the next cycle; a crash after `persist_events` but before `reader.commit()` re-emits at-least-once (persistence is de-duped by unique constraints).

---

## 3. Function-call flow — the Streamlit UI

`streamlit run app/ui/streamlit_app.py` boots on port 8501.

Direct dependencies on `app/*`:

| Import                                             | Purpose                                                        |
|----------------------------------------------------|----------------------------------------------------------------|
| `from app.db.session import SessionLocal`          | Opens SQLAlchemy sessions to run raw SQL                       |
| `from app.remediation.decisions import decide_action, VALID_STATES` | Approves / rejects / marks-done RemediationAction rows |
| `from app.observability import bootstrap, progress` | Configures telemetry + emits `APPROVAL` events                 |

Every page renders via two helpers ([streamlit_app.py:408-422](../app/ui/streamlit_app.py#L408-L422)):

```python
_query_df(sql, **params)      # returns pandas.DataFrame from a raw SQL
_query_scalar(sql, **params)  # returns a single scalar
```

There are **~35 `_query_df` / `_query_scalar` call sites** hitting these tables directly:

- `event_log`, `finding`, `root_cause_report`, `remediation_action`
- `process_case`, `process_definition`, `pipeline_event`, `historical_incident`

The UI only **writes** two things:
1. `RemediationAction.state` — via `decide_action(action_id, new_state, approver_id, note)` (also posts a ServiceNow comment).
2. `pipeline_event` — one `APPROVAL/succeeded` telemetry row when an operator approves (fail-safe try/except).

The UI is otherwise a pure viewer.

---

## 4. Module dependency graph

```
┌───────────────────────── app/readers/ ─────────────────────────┐
│ base.py               (BaseReader, EventLogPayload dataclass)  │
│ *_reader.py           (SimulatorFileReader, PrometheusReader,  │
│                        FluentdReader, MuleReader, PegaReader,  │
│                        AppianCSV/IntegrationTrace/             │
│                        ProcessMetrics/TaskErrors)              │
│ reader_state.py       (JSON checkpoint on disk)                │
│ persistence.py        (writes EventLog rows to DB)  ◄── the    │
│                                                       only     │
│                                                       DB       │
│                                                       import   │
└────────────────────────────────────────────────────────────────┘
   │ imports              yields EventLogPayload            │
   ▼                                                        ▼
┌── app/orchestrator/ ─────────────────────────────────────────┐
│ registry.py           (YAML → concrete classes)               │
│ dispatcher.py         (event_log → analyzers → sinks)         │
│ investigator_dispatcher.py (findings → grouper → investigator)│
│ remediation_dispatcher.py  (approved → LangGraph workflow)    │
│ db_reader.py, findings_reader.py, incident_grouper.py         │
└──────────────────────────────────────────────────────────────┘
       │                                       │
       │                                       ▼
       │            ┌── app/analyzers/  ─── app/sinks/ ───┐
       │            │   (per-source pattern detectors)     │
       │            │   (findings_emitter, console,         │
       │            │    servicenow_sink, report_sinks,     │
       │            │    remediation_action_sink)           │
       │            └──────────────────────────────────────┘
       ▼
┌── app/investigators/  ──►  app/servicenow_history/ (POINT 19)
│  base.py, llm_rca_       (loader/retrieval/indexer/feedback)
│  investigator.py         (hybrid vector + FTS search)
└─┬───────────────────────►  app/vectorstore/
  │                          (faiss / pgvector / qdrant)
  ▼
┌── app/remediation/ ──────────────────────────────────────────┐
│ langgraph_workflow.py    (VALIDATE→EXECUTE→VERIFY→REPORT)      │
│ executor.py              (Mock/DryRun/Real SSH)                │
│ verifier.py, decisions.py, allow_list.py, jira_feedback.py    │
└──────────────────────────────────────────────────────────────┘
       │                                       │
       ▼                                       ▼
┌── app/integrations/ ─────────┐ ┌── app/mcp_servers/ ────────┐
│  servicenow_client.py         │ │  jira_server.py (POINT 20) │
│  jira_mcp_client.py           │ └────────────────────────────┘
└──────────────────────────────┘

┌── app/observability/ ──── writes pipeline_event ── UI timeline ──┐
│  progress.py (stage/emit/generation) + telemetry.py (OTel/Langfuse)│
└──────────────────────────────────────────────────────────────────┘

┌── app/db/ ─── SQLAlchemy models + SessionLocal (Neon/Postgres) ──┐

┌── app/ui/streamlit_app.py ──►  DB (read)                           │
│                             ►  app.remediation.decisions (write)   │
│                             ►  app.observability.progress (write)  │
└────────────────────────────────────────────────────────────────────┘
```

---

## 5. Shared state — what all modules touch

| Kind              | What                                     | Producers        | Consumers                                 |
|-------------------|------------------------------------------|------------------|-------------------------------------------|
| Postgres tables   | `event_log`                              | persist_events   | orchestrator/dispatcher, UI               |
|                   | `finding`                                | findings_emitter | investigator_dispatcher, UI               |
|                   | `root_cause_report`                      | report sinks     | UI                                        |
|                   | `remediation_action`                     | action sink      | remediation_dispatcher, UI, decide_action |
|                   | `pipeline_event`                         | progress.emit    | UI (timeline)                             |
|                   | `historical_incident`                    | SNOW loader      | investigator (precedent retrieval)        |
| Filesystem input  | `data/input/{simulator,prometheus,fluentd,appian,mule,pega}/…` | external simulators / real integrations | Readers                     |
| Filesystem state  | `data/state/{source_type}.json`          | Readers (commit) | Readers (read on startup)                 |
| Filesystem output | `.data/precedent.faiss`                  | investigator     | investigator                              |
| Config            | `config/modules.yaml`                    | (hand-edited)    | Registry (orchestrator), progress (obs)   |
| Env / secrets     | `.env` locally, Azure Key Vault in cloud | ops              | DB, LLM, Jira, ServiceNow                 |

---

## 6. Package coupling summary

| Package              | Imports DB?    | Imports orchestrator? | Streamlit? | Notes                          |
|----------------------|----------------|-----------------------|------------|--------------------------------|
| `app/readers/base`   | No             | No                    | No         | Pure dataclass + ABC           |
| `app/readers/*reader`| No             | No                    | No         | Filesystem-bound               |
| `app/readers/reader_state` | No       | No                    | No         | JSON on disk                   |
| `app/readers/persistence`  | **Yes**  | No                    | No         | Writes EventLog rows           |
| `app/orchestrator/*` | Yes            | (self)                | No         | Also uses `readers.base`       |
| `app/analyzers/*`    | Yes (via ORM)  | No                    | No         |                                |
| `app/investigators/*`| Yes            | No                    | No         | Also LLM, vectorstore          |
| `app/remediation/*`  | Yes            | No                    | No         |                                |
| `app/sinks/*`        | Yes            | No                    | No         |                                |
| `app/observability/*`| Yes (lazy)     | No                    | No         | Fail-safe try/except           |
| `app/ui/streamlit_app`| **Yes** (direct SQL) | Only `remediation.decisions` | **Yes** | 35+ raw-SQL sites |

**Only two files import DB from within `app/readers/`**: `persistence.py` (writes) and nothing else. This is what makes the split feasible.

---

## 7. External processes (already running out-of-band)

- `app/data_simulator.py`, `app/pega_simulator.py` — synthetic-data generators that drop JSONL files into `data/input/*`. They are **not** started by the runner; you run them manually alongside the runner during demos.
- `app/mcp_servers/jira_server.py` — FastMCP HTTP server (port 8090) the remediation workflow talks to for Jira ticket creation (POINT 20).
- Real integrations (ServiceNow REST, Azure OpenAI, Langfuse OTLP) are called synchronously from within the runner process.

---

## 8. Constraints that will drive the split

1. **`app/readers/persistence.py` is the only reader-side file that imports `app.db.*`** — it belongs in the **core**, not the reader deployable. Everything else in `app/readers/` can move cleanly into its own service.
2. **Readers keep state locally** (`data/state/*.json` on disk). The reader container needs a persistent volume for those files, and a volume mount for `data/input/*`.
3. **Reader state advances only on `commit()`** — this is what makes at-least-once safe. The remote (HTTP) equivalent must preserve this contract: **ack before advancing**.
4. **The UI is read-heavy** — ~35 SQL query sites across ~8 tables. Any REST redesign for the UI has to cover every one of them, or bounce reads back through the DB.
5. **Writes from the UI are narrow** — `decide_action(...)` and `progress.emit("APPROVAL", …)`. Trivial to expose over REST.
6. **Postgres is the source of truth** — all three would-be containers must be able to reach it (either directly or via the core). Splitting the DB itself is out of scope.
7. **Config parity** — the reader deployable and the core deployable both need the `sources:` section of `modules.yaml`. Simplest is to ship the same file to both; changes are rare.
8. **Observability is opt-in and fail-safe** — the progress emitter already swallows errors. Both new services just need to call `progress.configure()` at boot.
9. **No back-references from core → reader**. Only direction of traffic is core polling reader, so the split is clean.
10. **Multi-tenancy** — every table and every payload is `tenant_id`-scoped. The REST contract must carry `tenant_id` on every endpoint.

---

## 9. Where this leads

Given (1)–(10), the natural split is three services + one shared Postgres:

- **`reader-service`** — FastAPI wrapper around `app/readers/`. Exposes `GET /sources`, `POST /sources/{type}/poll`, `POST /sources/{type}/ack`. **Polled** by the core.
- **`core-service`** — everything else in `app/*` except `ui/`. Runs the ingest → dispatch → investigate → remediate loop and a FastAPI surface for the UI (read endpoints + `POST /actions/{id}/decide`).
- **`ui-service`** — Streamlit only. Talks to core over HTTP; no DB, no SQLAlchemy.

The detailed target-state design is in [APIFICATION_PLAN.md](./APIFICATION_PLAN.md).
