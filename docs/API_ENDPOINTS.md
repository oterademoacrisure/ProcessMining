# API Endpoints — UI ⇄ Core ⇄ Reader

Reference for every HTTP endpoint exposed and consumed across the three
apification services in this repo. All routes are current as of branch
`reader_ui_apification`.

```
 ┌──────────────┐   HTTP    ┌────────────────┐   HTTP    ┌───────────────┐
 │  ui-service  │──────────▶│  core-service  │──────────▶│ reader-service│
 │  (Streamlit) │           │   (FastAPI)    │           │   (FastAPI)   │
 └──────────────┘           └────────────────┘           └───────────────┘
      port 8501                  port 8000                    port 8100
```

- **UI → Core**: named-view queries, one write for approvals, precedent memory.
- **Core → Reader**: pull-based ingest loop (poll → persist → ack).
- **Reader → DB**: none. Reader is stateless-to-Postgres by design.

---

## 1. reader-service (port 8100)

Source: [services/reader/main.py](services/reader/main.py)

Wraps `app/readers/`. Emits batches; commits reader state only on ack.

| Method | Path                              | Purpose                                                          |
|--------|-----------------------------------|------------------------------------------------------------------|
| GET    | `/health`                         | Liveness + pending batch count + configured `source_types`.      |
| GET    | `/sources`                        | List all `source_type`s the registry knows about.                |
| POST   | `/sources/{source_type}/poll`     | Read one batch; keep it pending in the in-memory `STORE`.        |
| POST   | `/sources/{source_type}/ack`      | Commit reader state for a previously polled `batch_id`.          |

### Request / response shapes

**`POST /sources/{source_type}/poll`** — [services/reader/main.py:85](services/reader/main.py#L85)
- Query: `tenant_id: int = 1`
- Response body (`PollResponse`):
  ```
  { "source_type": str, "tenant_id": int,
    "batch_id": str | null, "count": int,
    "events": [ EventLogPayload as dict, ... ] }
  ```
- `batch_id` is `null` when the reader produced zero events (nothing to ack).

**`POST /sources/{source_type}/ack`** — [services/reader/main.py:132](services/reader/main.py#L132)
- Body: `{ "batch_id": str }`
- Response (`AckResponse`): `{ "ok": bool, "committed": bool, "detail": str? }`
- `committed=false` means the batch id is unknown or was already acked (idempotent).

**Errors**: `404` for unknown `source_type`, `500` for reader build/read/commit failures.

---

## 2. core-service (port 8000)

Sources:
- Root: [services/core/main.py](services/core/main.py)
- API router: [services/core/api/routes.py](services/core/api/routes.py)
- Named-view registry: [services/core/api/query_views.py](services/core/api/query_views.py)

Two responsibilities: (a) the UI-facing HTTP surface under `/api/v1`, (b) a
background pipeline thread that pulls from reader-service.

### 2.1 Top-level

| Method | Path      | Purpose                                                   |
|--------|-----------|-----------------------------------------------------------|
| GET    | `/health` | Liveness + whether the background pipeline thread is up.  |

### 2.2 Named-view query — the UI's read escape hatch

**`POST /api/v1/query`** — [services/core/api/routes.py:61](services/core/api/routes.py#L61)

The UI never sends SQL. It names a view registered in `VIEWS`; the server
binds params and executes. The full registry:

| View name                                | Page / caller           | Notes                                              |
|------------------------------------------|-------------------------|----------------------------------------------------|
| `overview.event_count`                   | Overview                | scalar                                             |
| `overview.finding_count`                 | Overview                | scalar                                             |
| `overview.report_count`                  | Overview                | scalar                                             |
| `overview.case_count`                    | Overview                | scalar                                             |
| `overview.events_by_source`              | Overview                | table                                              |
| `overview.findings_by_source_severity`   | Overview                | table                                              |
| `overview.recent_reports`                | Overview                | last 10 RCA reports                                |
| `reports.list_by_severity`               | Root-Cause Reports      | filter by `sev: str[]`                             |
| `actions.for_report`                     | Report drilldown        | remediation actions for `rid`                      |
| `approvals.queue`                        | Pending Approvals       | filter by `states: str[]`                          |
| `findings.source_options`                | Findings                | dropdown values                                    |
| `findings.list_by_filter`                | Findings                | filter by `src: str[], sev: str[]`                 |
| `events.source_options`                  | Events Explorer         | dropdown values                                    |
| `events.explorer`                        | Events Explorer         | dynamic: optional `srv` (server_id `ILIKE`)        |
| `cases.list`                             | Cases                   | all cases + event counts                           |
| `cases.events_for_ref`                   | Cases                   | events for a given `case_reference_id`             |
| `live.pending_exec_count`                | Live Activity           | scalar                                             |
| `live.recent_correlations`               | Live Activity           | dynamic: optional `win` interval literal           |
| `live.timeline_for_corrs`                | Live Activity           | pipeline events for a set of `corrs`               |
| `live.awaiting_corrs`                    | Live Activity           | correlation ids blocked on approval                |
| `live.inprogress_corrs`                  | Live Activity           | correlation ids currently executing/verifying      |
| `live.rca_by_corr`                       | Live Activity           | RCA reports keyed by correlation id                |
| `actions.correlation_for_action`         | (internal telemetry)    | used server-side by `/actions/{id}/decide`         |

Every view takes `t` (tenant_id) as a required param. Extra params are
listed above. Dynamic views (`events.explorer`, `live.recent_correlations`)
are built by callables in [query_views.py](services/core/api/query_views.py);
`live.recent_correlations`'s `win` value is enforced against a whitelist.

**Request** (`QueryRequest`): `{ "view": str, "params": {...} }`
**Response** (`QueryResponse`): `{ "columns": [str], "rows": [[any, ...], ...] }`

**Errors**: `404` for unknown view, `400` for bad params (e.g. non-whitelisted
`win`), `500` on DB failure.

### 2.3 Approvals write path

**`POST /api/v1/actions/{action_id}/decide`** — [services/core/api/routes.py:98](services/core/api/routes.py#L98)

The only write the UI makes. Wraps
[`app.remediation.decisions.decide_action`](app/remediation/decisions.py).

- Body (`DecideRequest`): `{ "new_state": str, "approver_id": str = "anonymous", "note": str = "" }`
- Response (`DecideResponse`): `{ "ok": bool, "snow_posted": bool, "message": str }`
- Side effect: on `new_state == "approved"`, emits an `APPROVAL/succeeded`
  progress telemetry event (fail-safe; never breaks the response).

### 2.4 Precedent Memory (FAISS-backed)

Backing store: `.data/precedent.faiss` + `.data/precedent.faiss.meta.json`.

**`GET /api/v1/precedent/status`** — [services/core/api/routes.py:172](services/core/api/routes.py#L172)
- Query: `tenant_id: int = 1`
- Response (`PrecedentStatus`):
  ```
  { "backend": "faiss",
    "vectors_indexed": int,
    "counts": { "approved": int, "rejected": int, "unknown": int },
    "incidents": [ { "id": int, "ticket": str?, "outcome": str?,
                     "system": str?, "problem": str } ] }
  ```

**`POST /api/v1/precedent/search`** — [services/core/api/routes.py:223](services/core/api/routes.py#L223)
- Body (`PrecedentSearchRequest`): `{ "tenant_id": int, "query": str, "k": int = 5 }`
- Response (`PrecedentSearchResponse`):
  ```
  { "embedder_mode": str,
    "hits": [ { "id": int, "score": float, "number": str?,
                "cmdb_ci": str?, "outcome": str?,
                "short_description": str?, "close_notes": str? } ] }
  ```

---

## 3. ui-service (port 8501)

Sources:
- Streamlit entry: [services/ui/main.py](services/ui/main.py)
- HTTP client: [services/ui/api_client.py](services/ui/api_client.py)
- App: [app/ui/streamlit_app.py](app/ui/streamlit_app.py)

The UI exposes **no HTTP endpoints of its own** (Streamlit page routing only).
It is a pure consumer of core-service. Every call goes through
`services/ui/api_client.py`:

| Client function                          | Backing endpoint                                 |
|------------------------------------------|--------------------------------------------------|
| `query_df(view, params)`                 | `POST /api/v1/query`                             |
| `query_scalar(view, params)`             | `POST /api/v1/query`                             |
| `decide_action(action_id, ...)`          | `POST /api/v1/actions/{action_id}/decide`        |
| `precedent_status(tenant_id)`            | `GET  /api/v1/precedent/status`                  |
| `precedent_search(tenant_id, query, k)`  | `POST /api/v1/precedent/search`                  |

Base URL: `CORE_SERVICE_URL` env var, default `http://core-service:8000`.
Timeout: `CORE_HTTP_TIMEOUT`, default `30s`. ISO-8601 datetime columns
returned by `/query` are re-parsed to pandas `Timestamp`s by
`_decode_datetime_columns` in the client.

---

## 4. core-service → reader-service (server-to-server)

Client: [services/core/reader_client.py](services/core/reader_client.py)

Called from the background pipeline in
[services/core/pipeline.py](services/core/pipeline.py) (mirrors what
`app/runner.py :: _ingest_cycle` used to do in-process).

| Client method                          | Backing endpoint                        |
|----------------------------------------|-----------------------------------------|
| `list_sources()`                       | `GET  /sources`                         |
| `poll(source_type, tenant_id)`         | `POST /sources/{source_type}/poll`      |
| `ack(source_type, batch_id)`           | `POST /sources/{source_type}/ack`       |

Base URL: `READER_SERVICE_URL`, default `http://reader-service:8100`.

Semantics: `poll` returns a `batch_id` + list of `EventLogPayload` dicts;
core-service persists via `app.readers.persistence.persist_events` and only
then calls `ack`. If persistence fails, the batch stays pending in the
reader's in-memory `STORE` and will be re-issued on the next poll after
process restart.

---

## Environment variables (endpoint-related)

| Variable             | Default                           | Used by       |
|----------------------|-----------------------------------|---------------|
| `CORE_SERVICE_URL`   | `http://core-service:8000`        | ui-service    |
| `CORE_HTTP_TIMEOUT`  | `30`                              | ui-service    |
| `READER_SERVICE_URL` | `http://reader-service:8100`      | core-service  |
| `MODULES_YAML`       | `<repo>/config/modules.yaml`      | reader-service|
| `DISABLE_PIPELINE`   | *(unset)*                         | core-service — skip background ingest loop |
| `PORT`               | `8000` / `8100` / `8501`          | each service  |
