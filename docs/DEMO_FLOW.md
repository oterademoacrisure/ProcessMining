# Customer Demo Flow — Process Mining RCA (Architect's Guide)

A simple, customer-facing walkthrough of the **full platform**: the value, the
flow, and a ~7-minute live demo script. No code knowledge needed to present it.

---

## The 30-second pitch

> "When something breaks in production, the clues are scattered across many
> systems — the business process, the integration layer, the infrastructure —
> and no single tool sees the whole picture. Our platform automatically gathers
> clues from ALL of them, correlates them into one incident, diagnoses the root
> cause across layers, recommends the fix, files the ticket — and remembers how
> we fixed it last time. Hours of cross-team firefighting become one diagnosed
> incident."

---

## What it does, in one picture

```
   Appian (process / integration / task-errors)
   Mule (integration runtime)                      ALL sources, one platform
   Prometheus (infra metrics)                       |
   Fluentd (infra logs)                             |
        |                                           v
  1. INGEST     pull every source's logs into one place
        |
  2. DETECT     flag problems in each source
        |
  3. CORRELATE  group the related problems into ONE incident, across sources
        |
  4. DIAGNOSE   AI explains the root cause across pillars, using:
                  - Mule logs          -> WHERE the failure is (downstream vs us)
                  - infra (Prom/Fluentd) -> is the host/app itself the cause?
                  - ServiceNow history -> "we've seen this; here's the past fix"
        |
  5. ACT        file/update a ServiceNow ticket + recommend remediation
        |
  6. LEARN      the resolved ticket becomes precedent for next time
```

Hand-offs are database tables:
`event_log -> finding -> (incident) -> root_cause_report -> remediation_action`,
with `historical_incident` feeding precedent into step 4.

---

## Three pillars, one incident (the core message)

| Pillar | Sources | What it sees |
|---|---|---|
| Business process / integration | Appian x3, **Mule** | the process, the integration calls, user errors |
| Infrastructure / runtime | **Prometheus, Fluentd** | CPU/memory/pools, error logs |
| (Desktop) | — | not wired yet |

The platform's value is **correlating across these** — e.g. an incident where
Appian timeouts + Mule downstream errors + a Prometheus memory spike + Fluentd
OOM logs are recognized as **one problem**, and the AI decides whether the root
cause is the **downstream service** or **our own webapp under pressure** — and
backs it with a **matching past fix**.

---

## Live demo

### Before the meeting (5 min, off-screen)
```powershell
serverops\venv\Scripts\python.exe serverops\scripts\seed_demo_history.py    # load demo precedents
serverops\venv\Scripts\python.exe serverops\scripts\demo_full.py            # run ALL sources, one pass
cd serverops
venv\Scripts\python.exe -m streamlit run app\ui\streamlit_app.py            # leave the UI open
```
(`demo_full.py` = all real sources. Add `--focus` only if you want the simpler
Mule-only story instead.)

Check: the top report shows **5 sources** correlated, a cross-pillar summary,
and a "Similar past incidents" panel. Present the pre-run results — no need to
run live in front of the customer.

### In the meeting (~7 minutes)

**1. Frame it (talk, 45s)**
> "A production incident: credit-check calls timing out, processes piling up,
> users getting errors, and the host under memory pressure — all around the same
> time. Four different teams, four different dashboards. Watch the platform do it
> as one."

**2. Show the correlation** — Streamlit -> **Root-Cause Reports** -> expand the top report.
- Point at **incident_sources**: Appian + Mule + Prometheus + Fluentd in ONE incident.
  > "Five sources across three layers — stitched into a single incident, automatically."
- **Evidence chain**: walk the rows (each source contributing a clue).

**3. The cross-pillar diagnosis** — read the **summary**.
> "It didn't just list symptoms — it weighed them and concluded the root cause:
> the Appian webapp itself under JVM/connection-pool pressure, not the downstream
> service. That's the difference between paging the vendor and fixing our own host."

**4. The memory** — scroll to **"Similar past incidents."**
> "And it remembers: this matches INC0011880 — last time, we increased the JVM
> heap. The engineer starts with the proven fix, with a confidence score."

**5. The action** — point at **Recommended actions** (+ optional: open the real ServiceNow ticket).
> "It recommends the fix and files the ServiceNow ticket automatically."

**6. Close (talk, 30s)**
> "All your logs in; one diagnosed incident with the root cause across layers,
> the proven past fix, and a ready ticket out — no human triage. And every
> incident we resolve makes the next diagnosis smarter."

### The "wow" beats
- **"Five sources, one incident"** — cross-pillar correlation, automatic.
- **"It weighed the layers and picked the real cause"** — downstream vs our host.
- **"It remembered how we fixed it"** — precedent with a confidence %.
- **"It stays silent when unsure"** — no precedent below 60% confidence. (Trust.)

### If asked "is the AI guessing?"
> "No — detection and correlation are rule-based and deterministic. The AI only
> writes up evidence it's handed and cites real ticket numbers. It can't invent a
> correlation or a precedent."

### If asked "why isn't it always 100% sure?"
> "By design. When only one layer has evidence it stays cautious; when multiple
> layers agree it's confident. That honesty is what makes it trustworthy."

---

## Where each pipeline step shows in the UI

| Pipeline step | UI page | Point at | Say |
|---|---|---|---|
| 1. Ingest (raw logs, all sources) | **Events Explorer** (+ Overview "Events by source") | source-type filter; tick "Show metadata_json" | "Raw logs from every system, normalized into one place." |
| 2. Detect (findings per source) | **Findings** | each source / severity / observation | "It flags problems per source — Mule says 'credit-bureau timed out'." |
| 3. Correlate (grouping) | **Root-Cause Reports** -> expand a report | the "Incident — grouped N findings" line + `incident_sources` | "It stitched clues from several sources into ONE incident." |
| 4. Diagnose (root cause) | same report | the **Summary** | "It weighed the layers and named the root cause." |
| 5. Precedent (memory) | same report | the **Similar past incidents** panel | "It remembers — matches INC0011880 at 72%, here's the past fix." |
| 6. Act (fixes) | same report (**Recommended actions**) + **Pending Approvals** | the action list / approve buttons | "It recommends the fix and can file the ServiceNow ticket." |
| (How correlation works) | **Cases** -> drill into a Process ID | one job's timeline with Appian + Mule events together | "Same job, two sources, one timeline — that's how it links them." |

**Recommended page order:** Overview (the scale) -> Root-Cause Reports (expand
the top report and walk down it: grouped incident -> summary -> evidence chain
-> similar past incidents -> actions) -> Cases (the "how"). If a skeptic asks
for raw data, show Events Explorer + Findings.

**One-screen version:** if you show only one thing, expand the top
**Root-Cause Report** — scrolling down it IS the pipeline (grouped incident,
diagnosis, precedent, actions). Everything else is supporting detail.

---

## One-line summary
**All your logs in -> one diagnosed incident with the cross-layer root cause, the
proven past fix, and a ready-to-action ticket out — getting smarter every time.**

---

## Reference (for follow-up questions)
- Mule integration: `docs/MULE_INTEGRATION.md`
- ServiceNow precedent: `docs/SERVICENOW_HISTORY.md`
- Demo scripts: `scripts/demo_full.py` (all sources; `--focus` = Mule-only),
  `scripts/seed_demo_history.py`, `scripts/sync_servicenow_history.py`
