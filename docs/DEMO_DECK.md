---
marp: true
theme: default
paginate: true
header: 'Process Mining — Multi-Source Root-Cause Analysis'
footer: 'Confidential — Demo'
style: |
  section { font-size: 26px; }
  h1 { color: #1c66c9; }
  h2 { color: #0a2540; }
  table { font-size: 22px; }
  strong { color: #d6336c; }
---

<!--
Speaker notes appear here in Marp presenter view.
To export: install the "Marp for VS Code" extension, open this file,
then Export Slide Deck -> PowerPoint (.pptx) or PDF. Fully offline.
-->

# Process Mining
## Multi-Source Root-Cause Analysis

From scattered logs to a diagnosed incident — with the proven fix.

<!-- Opening line: "When production breaks, the answer is usually already in the logs — just scattered across systems no one person watches. This platform assembles it automatically." -->

---

## The problem

When production breaks:

- Clues are **scattered** across Appian, Mule, infrastructure — **no single tool sees the whole picture**
- **3–4 teams** spend **hours** manually correlating dashboards to find whose fault it is
- The knowledge of *"how we fixed this last time"* lives **in people's heads**

**Result:** slow MTTR, repeated firefighting, tribal knowledge.

<!-- Make it relatable: a credit-check outage where Appian, Mule, and infra each show a fragment, and four teams argue for an hour. -->

---

## The solution

> **Scattered logs in → one diagnosed incident with the root cause, the proven past fix, and a ready ServiceNow ticket out — in minutes, getting smarter every time.**

It automatically: **ingests** all sources → **correlates** related problems into one incident → **diagnoses** the root cause across layers → **recommends & files** the fix → **remembers** how it was resolved.

<!-- This is the elevator pitch slide. Pause here. -->

---

## Architecture

```
 SOURCES                TIER 1            TIER 2                 TIER 3
 Appian x3  ─┐        ┌─────────┐     ┌──────────────┐      ┌──────────────┐
 Mule       ─┤        │ ingest  │     │ group findings│      │ remediation  │
 Prometheus ─┼──────► │  +      ├────►│ -> incident   ├────► │ (human-gated)│
 Fluentd    ─┘        │ detect  │     │ -> LLM RCA    │      │ LangGraph    │
                      └─────────┘     └──────┬───────┘      └──────┬───────┘
                       event_log /           │ root_cause_report   │
                        finding              ▼                     ▼
                                       ServiceNow ◄── precedent ── historical_incident
                                       Streamlit UI ── approve ───────┘
```

*(High-res Mermaid version in `docs/architecture.md` — export PNG and drop here.)*

<!-- Walk left to right: sources -> ingest/detect -> correlate/diagnose -> remediate. Note the two feedback loops: ServiceNow precedent in, approvals from the UI. -->

---

## How it works — 6 steps

1. **Ingest** — pull every source's logs into one store
2. **Detect** — flag problems per source (rule-based)
3. **Correlate** — group related problems into **one incident**, across systems
4. **Diagnose** — AI explains the root cause, using Mule + infra + past incidents
5. **Act** — recommend the fix, file the ServiceNow ticket
6. **Learn** — the resolved ticket becomes precedent for next time

<!-- Emphasize 3 and 6: cross-source correlation is the engine; learning is the compounding value. -->

---

## Differentiator 1 — Mule pinpoints *where*

Appian/infra can see *that* an integration is unhealthy. **Mule sees *where* it failed:**

- `SocketTimeoutException` → the **downstream service** didn't respond
- `flow.error` before any outbound call → **Mule itself**

> Turns *"an integration is unhealthy"* into *"credit-bureau-prod is down — page them; don't retry our side."*

<!-- The diagnosis shifts from "probable" to "definitive" — that's what Mule logs unlock. -->

---

## Differentiator 2 — Institutional memory

When a new incident appears, it finds **similar past incidents + their fixes**, scored by **confidence**:

```
📚 Similar past incidents
  INC0009999 · credit-bureau-prod · confidence 98% · "paged vendor, restarted gateway"
```

- **≥ 0.60 confidence** to surface — **stays silent when unsure** (no misleading precedent)
- Recommends an action **based on the proven fix**, citing the ticket

<!-- This is the "gets smarter every time" slide. Confidence + silence-when-unsure = trust. -->

---

## Remediation — human in the loop

- The AI **recommends**; a human **approves** in the UI
- Approved fixes run through a **LangGraph workflow**: **validate → execute → verify → report**
- Safety: an **allow-list** gates commands; progress is posted back to the ServiceNow ticket
- **Never auto-remediates without approval**

<!-- Stress safety: deterministic gate + human approval + verify step. -->

---

## Live demo — real data, end to end

From one run on sample production data:

- **188 events** from **6 sources** ingested
- → **10 findings** → grouped into **3 incidents**
- → cross-pillar root cause (Appian + Mule + Prometheus + Fluentd in **one** incident)
- → matching precedent at **98% confidence**, recommended remediation, auto-ticketed

<!-- These are real numbers from the actual pipeline, not a mockup. -->

---

## The value

| Value | Why it matters |
|---|---|
| **Faster MTTR** | Diagnosed in minutes, not hours — no manual triage |
| **Right team, right fix** | Downstream vs. our side — no wasted effort |
| **Institutional memory** | Proven past fixes reused, confidence-scored |
| **Trustworthy** | Cites real evidence; silent when unsure; human-gated |

<!-- Tie each row to a moment they just saw in the demo. -->

---

## Why it's trustworthy

- **Detection & correlation are deterministic** (rule-based) — not the AI guessing
- The AI **only narrates evidence it's given** and **cites real ticket numbers**
- **Honest confidence** — cautious with one pillar, confident when layers agree
- **No precedent below 0.60** — and **no remediation without approval**

<!-- Pre-empt the "is the AI hallucinating?" question. -->

---

## Roadmap

- Real-time streaming connectors (live Anypoint / Appian feeds)
- **Embedding-based** precedent reranking (semantic match)
- **Native ServiceNow approval** workflow integration
- Desktop-activity pillar (Soroco / ActivTrak)

## Thank you

> Logs in → diagnosed incident, proven fix, ready ticket — getting smarter every time.

<!-- Close on the one-liner. Offer to show the live UI. -->
