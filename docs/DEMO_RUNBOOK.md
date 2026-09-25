# Demo Runbook — Run From Scratch

Wipe all data and run the full precedent-RCA demo end to end, one step at a time.

## Prerequisites (check once)
- **Config** (`config/modules.yaml`):
  - `servicenow_mode: real` → real tickets in your dev ServiceNow (`mock` = offline)
  - `executor_mode: mock` → remediation command is **simulated** ("assume fixed")
  - `vector_backend: faiss`
  - **Task #20 (Jira):** `jira_enabled: true`, `jira_mcp_server: own`, `jira_mcp_transport: http`, `jira_project_key: KAN`
- **`.env`** has: `DATABASE_URL` (Neon Postgres), `AZURE_OPENAI_*` (embeddings + LLM),
  `SERVICENOW_*` (real mode), and `JIRA_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` (real Jira).

**Run every command from the serverops folder with PYTHONPATH set:**
```powershell
cd C:\Dewasheesh\processMining_15June2026\serverops
$env:PYTHONPATH = (Get-Location).Path
```

### ⭐ Start the OWN Jira MCP server (SECOND terminal — keep it running through Step 7)
**Required before Step 7.** We use **Variant A — our own local FastMCP server**
(`jira_mcp_server: own` in `config/modules.yaml`), which is fully unattended (API token,
no browser). Task #20 files a Jira ticket per command execution through this local web
service, so start it **now** in a dedicated terminal and **leave it open** until the demo
is done:
```powershell
cd C:\Dewasheesh\processMining_15June2026\serverops
$env:PYTHONPATH = (Get-Location).Path
python app\mcp_servers\jira_server.py --http      # listens on http://127.0.0.1:8090/mcp
```
Verify it bound: `http://127.0.0.1:8090/mcp` (the log prints "Uvicorn running on http://127.0.0.1:8090").
If it's down, Jira ticketing just **fails safe** (no ticket; remediation still completes).
*(This is **Variant A** — our own server, the **recommended unattended default**. **Variant B**
(Atlassian's hosted MCP) is optional and, as of now, **needs a fresh one-time
`scripts\authorize_atlassian.py` browser login** before use — its cached token was invalidated by
the `/v1/sse → /v1/mcp` endpoint change. See the section after Step 8.)*

---

## Step 1 — Wipe ALL data
```powershell
# clear remediation actions (child of reports)
.\venv\Scripts\python.exe -c "from app.db.session import SessionLocal; from sqlalchemy import text; s=SessionLocal(); print('remediation_action deleted:', s.execute(text('DELETE FROM remediation_action')).rowcount); s.commit(); s.close()"

# reset the live pipeline tables (event_log / finding / root_cause_report / process_*)
.\venv\Scripts\python.exe app\dev_reset.py

# wipe the precedent corpus (historical_incident)
.\venv\Scripts\python.exe -c "from app.db.session import SessionLocal; from sqlalchemy import text; s=SessionLocal(); print('historical_incident deleted:', s.execute(text('DELETE FROM historical_incident')).rowcount); s.commit(); s.close()"
```

## Step 2 — Seed precedent corpus + build the vector index
```powershell
.\venv\Scripts\python.exe scripts\seed_demo_history.py --tenant 1      # 8 curated incidents (+ outcomes)
.\venv\Scripts\python.exe scripts\build_vector_index.py                # embed -> FAISS
.\venv\Scripts\python.exe scripts\inspect_vector_index.py              # expect: 8 incidents
```

## Step 3 — Run the pipeline (ingest -> detect -> diagnose -> ticket)
```powershell
.\venv\Scripts\python.exe scripts\demo_full.py --keep-servicenow --log-level INFO
```
Observe: events ingested -> findings -> incidents grouped -> LLM diagnosis (citing precedents)
-> **real `INC…` tickets created** -> recommended actions. Watch the
`app.servicenow_history.retrieval` log lines (the hybrid search arms).

> **All sources ingested (no focus).** Run **without `--focus`** (as in the command
> above) so `demo_full.py` reads **every registered source** in one pass — Appian ×3,
> Mule, Prometheus, Fluentd, **and Pega** (`data/input/pega/pega_integration_trace.csv`).
> **Pega needs no separate simulator call.** Its `CreditCheckService` times out against
> the same credit bureau on the **same Case IDs (= Appian Process IDs) and time window**,
> so persist_events resolves them to one `case_id` and the incident grouper **merges Pega
> into the SAME cross-source incident** — it appears as **one extra source in the existing
> RCA report, NOT a separate Pega report** (look for `pega_integration_trace` in the
> report's `incident_sources`).
> *(Do not pass `--focus`. The Pega CSV is curated to share Appian's case IDs — do not
> overwrite it with `pega_simulator.py`, which emits independent case IDs and would split
> Pega into its own incident.)*

## Step 4 — Inspect what the run wrote
```powershell
.\venv\Scripts\python.exe scripts\inspect_run.py
```
Shows table counts, each report (+ real `servicenow_number`), the **precedent block** fed to the
LLM (with outcomes), and the recommended actions.

## Step 5 — Launch the UI
```powershell
.\venv\Scripts\streamlit.exe run app\ui\streamlit_app.py
```
Open `http://localhost:8501`, sidebar **Tenant ID = 1**. Explore:
- **Root-Cause Reports** → precedent panel + outcome badges + ServiceNow link
- **Pending Approvals** → the recommended fixes
- **Precedent Memory** → browse + semantic-search the index

## Step 6 — Approve an executable fix (in the UI)
On **Pending Approvals** (or a report's actions), find an action with a **`$ command`**
(executable) and click **Approve**. *(Advisory actions are tracking-only — they won't auto-run.)*

## Step 7 — Execute the remediation (one cycle)
> ⚠️ **Confirm the OWN Jira MCP server is still running** (the second-terminal step from setup,
> on `http://127.0.0.1:8090/mcp`). If it's down, the fix still executes and verifies, but **no
> Jira ticket is filed** (fails safe).
```powershell
.\venv\Scripts\python.exe scripts\run_remediation_once.py
```
Observe: **VALIDATE -> EXECUTE (mock) -> VERIFY -> verified**, then the closed-loop sequence
runs **in this order**:
1. ⭐ `create_jira_ticket … -> Jira KAN-… (real)` (**Task #20** — the command's audit ticket is
   filed via the MCP web service) and, because the fix verified, it is **transitioned to `Done`**;
   the action's `jira_key` is set. (A *failed/unverified* fix leaves the ticket **Open**.)
2. `resolve_incident … Resolved` (**200**) — the ServiceNow ticket is resolved **citing the Jira
   key** in its close-notes (e.g. `… Jira: KAN-8.`), so the two records are cross-linked.
3. ⭐ `capture_precedent … outcome=approved -> historical_incident=NN` (learning loop writes the new
   precedent to Postgres + FAISS).

Open the **KAN-…** issue in Jira — status should be **Done** — and the **INC** in ServiceNow to see
the close-notes link back to it.

## Step 8 — Confirm the loop closed
```powershell
.\venv\Scripts\python.exe scripts\inspect_vector_index.py
```
Corpus grew **8 -> 9** with a new `RCA-<report_id>` (outcome **approved**), linked to the real INC.
Open that **INC** in ServiceNow → see the journal comments + **state = Resolved** + close notes.

---

---

## Variant B — file the Jira audit ticket via Atlassian's HOSTED MCP (optional)
Steps 1–8 use **Variant A** (`jira_mcp_server: own`) — our local FastMCP server, API token, fully
unattended. To demo the **same flow** through Atlassian's official hosted MCP instead:

1. **Authorize once (browser):**
   ```powershell
   .\venv\Scripts\python.exe scripts\authorize_atlassian.py
   ```
   Log in + consent. The OAuth token is cached in the Windows Credential Manager (keyring); later
   runs refresh it silently.
2. **Switch the backend** in `config/modules.yaml`: `jira_mcp_server: atlassian`
   (endpoint `jira_atlassian_url: https://mcp.atlassian.com/v1/mcp` — the Streamable-HTTP endpoint;
   the old `/v1/sse` is deprecated after 2026-06-30).
3. **Approve another executable action**, then run remediation again:
   ```powershell
   .\venv\Scripts\python.exe scripts\run_remediation_once.py
   ```
   Observe `create_jira_ticket … -> Jira KAN-… (atlassian)`. The ticket is created via
   `createJiraIssue`, then moved to **Done** via `getTransitionsForJiraIssue` +
   `transitionJiraIssue {id:31}`. ServiceNow is resolved citing the new Jira key — exactly as Variant A.
4. **Switch back** to `jira_mcp_server: own` for the unattended default.

> ⚠ **Automation caveat:** Part B's first-time OAuth **requires a browser** (Atlassian hosts only
> *user* OAuth — no service account). After that it refreshes silently, but if the refresh token
> expires / is revoked it needs the browser again, and headless/CI can't pop one. **So Part B is not
> truly zero-touch** — **Variant A (API token in Key Vault) is the fully unattended path.**

---

## What you've demonstrated
`detect → diagnose (hybrid precedent search, 0.60 bar) → real ServiceNow ticket → human approves →
mock fix → verify → Jira ticket filed + marked Done (audit of the command, via MCP) → ServiceNow
ticket Resolved citing the Jira key → incident learned as a new precedent` — the full closed loop,
cross-linked across Jira and ServiceNow, with a **switchable MCP backend** (own ⇄ Atlassian hosted).

## Diagram
See **`docs/DEMO_LLD.pptx`** — Slide 1 (end-to-end closed loop) and Slide 2 (MCP integration:
Part A own server vs Part B Atlassian hosted, with auth / transport / automation notes).

## Reset to mock (optional)
To run fully offline (no real tickets), set `servicenow_mode: mock` in `config/modules.yaml`.
