# APIFICATION Plan — Three deployables over REST

> Target-state design for splitting the ServerOps platform into three independent
> containers. Cross-linked with [CURRENT_STATE_AND_CALL_FLOW.md](./CURRENT_STATE_AND_CALL_FLOW.md).
>
> All task-related code changes carry the marker `# APIFICATION:` per the project convention.

---

## 1. The split

```
┌──────────────────────────┐            ┌────────────────────────────────────┐
│      ui-service          │  HTTP      │           core-service              │
│   (Streamlit, port 8501) │◄──────────►│  FastAPI, port 8000                 │
│                          │            │  + background pipeline loop         │
└──────────────────────────┘            │                                     │
                                        │  - orchestrator/                    │
                                        │  - analyzers/                       │
                                        │  - investigators/                   │
                                        │  - remediation/                     │
                                        │  - sinks/                           │
                                        │  - integrations/, mcp_servers/     │
                                        │  - servicenow_history/, vectorstore/│
                                        │  - observability/                   │
                                        │  - readers/base.py + persistence.py│
                                        │                                     │
                                        │  ── ↕ polls ↕ ──                    │
                                        └────────────┬───────────────────────┘
                                                     │ HTTP (polling)
                                                     ▼
                                        ┌────────────────────────────────────┐
                                        │        reader-service               │
                                        │   FastAPI, port 8100                │
                                        │   - readers/*_reader.py             │
                                        │   - readers/base.py                 │
                                        │   - readers/reader_state.py         │
                                        │   - readers/timestamps.py           │
                                        │   - config/modules.yaml (sources:)  │
                                        │   - data/input/**, data/state/**    │
                                        └────────────────────────────────────┘

┌──────────────────── shared Postgres (single source of truth) ────────────────┐
│  core-service is the only writer / reader of DB.                              │
│  reader-service does NOT touch Postgres.                                      │
│  ui-service does NOT touch Postgres — everything via core-service REST.       │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Direction of traffic:**

- `core-service` → polls → `reader-service`   (data ingestion)
- `ui-service` → calls → `core-service`      (all reads + operator writes)
- Nothing calls the UI. Nothing calls the reader other than the core.

---

## 2. Contracts (REST)

### 2.1 `reader-service` — polled by the core

Base path: `/`

| Method | Path                                     | Purpose                                   |
|--------|------------------------------------------|-------------------------------------------|
| GET    | `/health`                                | Liveness                                  |
| GET    | `/sources`                               | List configured `source_type`s            |
| POST   | `/sources/{source_type}/poll?tenant_id=` | Run `reader.read()` once, return payloads |
| POST   | `/sources/{source_type}/ack`             | `reader.commit()` after successful persist |

**`POST /sources/{source_type}/poll` response**

```json
{
  "source_type": "simulator",
  "tenant_id": 1,
  "batch_id": "simulator-1-c2b6f6b0-...",
  "events": [
    {
      "tenant_id": 1,
      "source_type": "simulator",
      "timestamp": "2026-09-01T12:34:56+00:00",
      "activity_name": "metric_sample",
      "activity_type": "automated",
      "lifecycle_stage": "complete",
      "actor_id": null,
      "system_id": "orders",
      "server_id": "svr-01",
      "sequence_number": null,
      "duration": null,
      "case_id": null,
      "process_id": null,
      "metadata_json": { "cpu": 0.83, "..." : "..." },
      "process_definition_name": null,
      "case_reference_id": null
    }
  ]
}
```

**`POST /sources/{source_type}/ack` body**

```json
{ "batch_id": "simulator-1-c2b6f6b0-..." }
```

**Semantics — preserving at-least-once:**

The reader keeps a pending batch in memory keyed by `batch_id`. `commit()` runs only after the client acks. If the client never acks (crash, network loss), the next `poll` re-reads the same records because `reader_state` has not been advanced. This is exactly what `runner.py` does today, just moved across the wire.

If `poll` is called again before the previous batch is acked, the previous batch is dropped from memory and the new batch supersedes it (same as calling `reader.read()` twice today).

### 2.2 `core-service` — called by the UI

Base path: `/api/v1/`

| Method | Path                                     | Purpose                                        |
|--------|------------------------------------------|------------------------------------------------|
| GET    | `/health`                                | Liveness                                       |
| GET    | `/overview?tenant_id=`                   | Header counters (events/findings/reports/cases)|
| GET    | `/events?tenant_id=&limit=&…`            | `event_log` rows                               |
| GET    | `/findings?tenant_id=&…`                 | `finding` rows                                 |
| GET    | `/reports?tenant_id=&…`                  | `root_cause_report` rows                       |
| GET    | `/reports/{report_id}/actions`           | `remediation_action` rows for a report         |
| POST   | `/actions/{action_id}/decide`            | Approve / reject / mark-done (wraps `decide_action`) |
| GET    | `/pipeline_events?tenant_id=&…`          | Live timeline                                  |
| GET    | `/historical_incidents?…`                | Precedent viewer                               |
| POST   | `/query` (server-whitelisted views)      | **Escape hatch** for the long tail of UI SQL   |

**Rationale for `POST /query`:** The UI has ~35 raw-SQL sites and rebuilding each as a dedicated endpoint balloons the surface area. Instead, we keep a server-side dictionary of **named views** (`{name: sql_template, allowed_params: [...]}`), and the UI sends `{name, params}`. Anything not in the whitelist is rejected. This is a pragmatic middle ground — REST contract is preserved, no SQL travels over the wire, and we don't have to invent 30+ endpoints on day one. Named views can be promoted to first-class GET endpoints incrementally.

`POST /actions/{action_id}/decide` body:

```json
{ "new_state": "approved", "note": "...", "approver_id": "alice" }
```

Response:

```json
{ "ok": true, "snow_posted": true, "message": "..." }
```

Behind the scenes the endpoint calls the existing `app.remediation.decisions.decide_action(...)` unchanged, then fires the `APPROVAL` `progress.emit(...)` — so the UI's fail-safe try/except moves server-side and stays fail-safe.

---

## 3. Package layout after the split

```
serverops/                         # (repo root — unchanged)
├── services/                      # NEW: per-container entry points
│   ├── reader/
│   │   ├── main.py                # FastAPI app for reader-service
│   │   ├── batch_store.py         # In-memory pending batches by batch_id
│   │   ├── Dockerfile
│   │   └── requirements.txt       # readers-only deps
│   ├── core/
│   │   ├── main.py                # FastAPI app + background pipeline loop
│   │   ├── reader_client.py       # HTTPX client that polls reader-service
│   │   ├── api/
│   │   │   ├── overview.py
│   │   │   ├── events.py
│   │   │   ├── findings.py
│   │   │   ├── reports.py
│   │   │   ├── actions.py
│   │   │   ├── pipeline.py
│   │   │   └── query_views.py     # server-side named-view whitelist
│   │   ├── Dockerfile
│   │   └── requirements.txt       # everything except streamlit
│   └── ui/
│       ├── main.py                # thin wrapper that runs streamlit
│       ├── api_client.py          # replaces `_query_df` / `_query_scalar`
│       ├── Dockerfile
│       └── requirements.txt       # streamlit + plotly + httpx only
│
├── app/                           # unchanged Python packages (shared source)
│   ├── readers/                   # bundled into reader-service container
│   │   ├── base.py                # ALSO bundled into core (for EventLogPayload)
│   │   ├── persistence.py         # STAYS with core (imports app.db.models)
│   │   ├── reader_state.py
│   │   ├── timestamps.py
│   │   └── *_reader.py
│   ├── db/                        # bundled into core-service only
│   ├── orchestrator/              # bundled into core-service only
│   ├── analyzers/                 # bundled into core-service only
│   ├── investigators/             # bundled into core-service only
│   ├── remediation/               # bundled into core-service only
│   ├── sinks/                     # bundled into core-service only
│   ├── integrations/, mcp_servers/, servicenow_history/, vectorstore/, mule/, utils/
│   ├── observability/             # bundled into core AND reader (for progress.configure)
│   ├── ui/                        # bundled into ui-service only
│   ├── secrets.py                 # bundled into every container
│   └── paths.py                   # bundled into every container
│
├── config/
│   └── modules.yaml               # bundled into reader (sources:) AND core (full)
│
├── data/                          # host-mounted volumes into reader-service
│   ├── input/                     # source-specific input dirs
│   └── state/                     # reader checkpoints
│
├── docs/                          # incl. this file + CURRENT_STATE_AND_CALL_FLOW.md
├── docker-compose.yml             # NEW: brings up all three + local Postgres for dev
└── alembic/, tests/, scripts/, …  # unchanged
```

**Why keep one `app/` tree** — the shared source keeps `git blame`, imports, and tests unchanged. Each container's Dockerfile `COPY`s only the subset it needs. No per-service Python packaging boilerplate.

---

## 4. Core pipeline loop after the split

`services/core/main.py` runs FastAPI + a background thread that mirrors `app/runner.py`:

```python
# APIFICATION: pipeline background loop — same cycle as app/runner.py, but ingest
# hits reader-service over HTTP instead of building readers locally.

async def pipeline_loop(...):
    while not stop:
        for source_type in reader_client.list_sources():
            batch = await reader_client.poll(source_type, tenant_id)
            if not batch.events:
                continue
            payloads = [EventLogPayload(**e) for e in batch.events]
            with SessionLocal() as s:
                persist_events(s, payloads)         # unchanged
            await reader_client.ack(source_type, batch.batch_id)

        with SessionLocal() as s:
            dispatcher.run_once(s)
            inv_dispatcher.run_once(s)
            rem_dispatcher.run_once(s)

        await asyncio.sleep(interval_sec)
```

Everything from `_dispatch_cycle` downstream is unchanged — same registry, same dispatchers, same DB — because that half of the pipeline never crossed the reader boundary.

`app/runner.py` remains in the repo but is deprecated for container deployments (kept for `python app/runner.py` local single-process debugging).

---

## 5. UI after the split

`_query_df` / `_query_scalar` in [app/ui/streamlit_app.py](../app/ui/streamlit_app.py) become thin wrappers over `services/ui/api_client.py`:

```python
# APIFICATION: UI never opens a DB session — all reads go over REST.
def _query_df(sql: str, **params) -> pd.DataFrame:
    view_name = _NAMED_VIEW_LOOKUP[sql]  # SQL string -> registered view name
    return api_client.query_view(view_name, params)

def _query_scalar(sql: str, **params):
    return api_client.query_view_scalar(_NAMED_VIEW_LOOKUP[sql], params)
```

We keep a **module-level dict `_NAMED_VIEW_LOOKUP`** that maps the literal SQL string used at each call site to a whitelist name. This lets us leave the ~35 SQL strings in place as documentation and change only the two helpers. The view whitelist lives in `services/core/api/query_views.py` and is authoritative.

**Writes** — `decide_action(...)` is replaced with `api_client.decide_action(action_id, new_state, note)` which POSTs to core. `progress.emit("APPROVAL", ...)` also moves server-side, called by the endpoint.

---

## 6. Dependencies per container

| Package                    | reader | core | ui |
|----------------------------|:------:|:----:|:--:|
| fastapi, uvicorn           |  ✅    |  ✅  |    |
| httpx                      |  ✅    |  ✅  | ✅ |
| PyYAML                     |  ✅    |  ✅  |    |
| python-dotenv, azure-*     |  ✅    |  ✅  | ✅ |
| pydantic                   |  ✅    |  ✅  | ✅ |
| sqlalchemy, psycopg2, alembic |     |  ✅  |    |
| openai, langgraph          |        |  ✅  |    |
| faiss-cpu, numpy           |        |  ✅  |    |
| fastmcp                    |        |  ✅  |    |
| opentelemetry-sdk/exporter |        |  ✅  |    |
| streamlit, plotly, streamlit-autorefresh |||✅|
| pandas                     |        |      | ✅ |

`requirements.txt` is split into three files under `services/*/requirements.txt`. The root-level `requirements.txt` stays for local single-process dev (unchanged).

---

## 7. Configuration

- Reader container reads `config/modules.yaml` for the `sources:` block only.
- Core container reads the full file (everything else lives there).
- Both mount the file read-only.
- Env vars per container:
  - **reader-service**: `TENANT_ID` (default 1), volume mounts for `data/input` + `data/state`
  - **core-service**: `DATABASE_URL`, `AZURE_OPENAI_*`, `SERVICENOW_*`, `JIRA_*`, `LANGFUSE_*`, `READER_SERVICE_URL` (e.g. `http://reader-service:8100`), `INTERVAL_SEC`
  - **ui-service**: `CORE_SERVICE_URL` (e.g. `http://core-service:8000`), `TENANT_ID`

---

## 8. Volumes and secrets

- `data/input/**` — bind-mount from host into `reader-service`. Same tree the simulators write into.
- `data/state/**` — named volume for `reader-service` so checkpoints survive restarts.
- `.data/precedent.faiss` — named volume for `core-service` (vector index).
- `.env` — bind-mount into each container that needs the vars, or supply via `--env-file` in compose.
- Azure Key Vault path (`AZURE_KEY_VAULT_URL`) works unchanged in each container.

---

## 9. Migration steps (staged, reversible)

1. **[core-side, no behavior change]** Copy `EventLogPayload` into a smaller shared module (`app/readers/base.py` already isolates it). Add a `to_dict()` / `from_dict()` helper for JSON transport.
2. **[core-side]** Add `services/core/main.py` with FastAPI, mount all UI endpoints, wire the background pipeline loop to use the *local* Registry.build_reader (still single-process). Verify parity with `python -m services.core.main` vs. `python app/runner.py`.
3. **[reader-side]** Add `services/reader/main.py`. Import `Registry` from orchestrator to reuse the config parser but only touch its `source_types` / `build_reader`. Run it standalone; smoke-test the JSON payload shape.
4. **[core-side]** Add `services/core/reader_client.py`, switch the background loop to poll reader-service. Old `_ingest_cycle` path is retired.
5. **[ui-side]** Add `services/ui/api_client.py`, replace `_query_df` / `_query_scalar` bodies, remove `from app.db.session import SessionLocal` and `from app.remediation.decisions import decide_action` from `app/ui/streamlit_app.py`.
6. **[containerization]** Add three `Dockerfile`s and one `docker-compose.yml`. Local dev = `docker compose up`.
7. **Rollback** — every step above is one PR. Steps 4–5 can each be reverted independently because the core-service still contains the full pipeline; a reader failure just makes ingest go to zero.

---

## 10. Non-goals for this pass

- Splitting Postgres. One DB stays. Multi-DB is a separate initiative.
- Splitting the core further (ingest / correlate / remediate as three services). Doable later, but out of scope now — the existing dispatchers are cheap enough to co-exist.
- Auth / TLS between services. Compose network is trusted for MVP; add auth in the next iteration.
- Replacing the LangGraph workflow. Untouched.
- Retiring `app/runner.py`. Kept for local single-process debugging.

---

## 11. Open questions for the user

Captured in the AskUserQuestion pass before implementation. Summary:

1. **UI read strategy** — named-view escape hatch (recommended) vs. one endpoint per query vs. shared DB read for UI.
2. **Docker artifacts** — produce `Dockerfile`s + `docker-compose.yml` now, or code-only refactor?
3. **Preserve `app/runner.py`** — keep it as a local single-process debug entry point, or delete after cutover?
