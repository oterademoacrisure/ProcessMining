# Mule Integration — Demo Script (teleprompter)

A 5–7 minute live demo of Task #14. Read the **SAY** lines aloud; run the
**DO** commands. Expected output is noted so you know it's on track.

> One-line pitch: *"We could already tell something broke in the Appian
> integration layer. By adding Mule logs, the system now tells us exactly
> WHERE — and whether to page the downstream team or fix our own side."*

---

## 0. Prep (10 minutes before — not in front of the audience)

**DO** (from repo root `C:\Dewasheesh\processMining_15June2026`):
```powershell
# Warm-up run so the DB + LLM path are primed and you've seen the output
serverops\venv\Scripts\python.exe serverops\scripts\demo_full.py --focus
```
**DO** — leave the UI running in a browser tab:
```powershell
cd serverops
venv\Scripts\python.exe -m streamlit run app\ui\streamlit_app.py
```
Check: the warm-up printed a report flagged `<== MULE + APPIAN MERGED`, and the
UI opened in the browser. If yes, you're ready.

---

## 1. Frame the problem  (~45 sec, talk only)

**SAY:** "Here's a real incident from our data. At 2:22pm a credit-check
integration in Appian started timing out. The Appian logs can see *that* it
timed out — but not *why*. Is the external credit-bureau service down? Is our
Mule integration layer broken? Is it our own server? Each needs a different
team and a different fix. Until now, the system couldn't tell them apart."

---

## 2. Run it live  (~2 min)

**DO:**
```powershell
serverops\venv\Scripts\python.exe serverops\scripts\demo_full.py --focus
```

Narrate as the stages print:

- **STAGE 1** — **SAY:** "First it ingests the Mule logs right alongside the
  Appian logs into one table."
  *Expect:* `mule 89`, `appian_integration_trace 20`, `appian_task_errors 12`.

- **STAGE 2** — **SAY:** "Then it flags the problems. Look at the Mule flag — it
  says the failure *localizes to credit-bureau-prod*, from a SocketTimeout. That's
  a deterministic rule, not a guess."
  *Expect:* a `mule / critical / credit-bureau-prod` finding with that observation.

- **STAGE 3+4** — **SAY:** "And here's the key move — it recognized the Mule
  finding and the Appian finding are about the *same jobs*, and stapled them into
  ONE incident."
  *Expect:* a report line with `incident_sources = ['appian_integration_trace',
  'appian_task_errors', 'mule']` and the `<== MULE + APPIAN MERGED` flag.

**SAY (read the summary aloud):** "...and the conclusion names the culprit:
the downstream credit-bureau service. The recommended action is to restore/scale
*that* service — not to retry Appian."

---

## 3. Show the screen  (~1–2 min)

**DO:** switch to the Streamlit tab → open the most recent report.

**SAY:** "Same story, visually: one incident, evidence from multiple sources
stitched together, the root cause, and the concrete remediation an operator can
approve."

Point at: the multi-source evidence chain, the downstream-localized summary, the
recommended actions.

---

## 4. Close  (~30 sec)

**SAY:** "So we went from 'something is wrong in the integration layer' to
'the credit-bureau service is the root cause — here's who to page.' That
diagnosis shift is exactly what the Mule logs unlock, and it slotted into the
existing pipeline without changing the core engine."

---

## Q&A — likely questions

- **"Did you change the core system?"**
  No. Mule plugged in as a config entry plus a reader/analyzer, exactly like the
  existing Appian sources. The incident grouper, investigator, and database were
  untouched.
- **"How does it know the Mule and Appian events are related?"**
  Mule events carry the Appian Process ID, so they resolve to the same internal
  `case_id`. The existing grouper links any findings that share a case + time.
- **"Is the AI just making this up?"**
  No. The localization ("SocketTimeout → downstream") is rule-based logic. The AI
  only narrates evidence it's given and cites real event IDs.
- **"Why isn't it 100% confident?"**
  By design — Mule and Appian are the same 'pillar', so it stays cautious until a
  different pillar (infra) confirms. That restraint keeps it trustworthy.

---

## Contingencies

- **Network/LLM flaky during the talk?** Don't run live — present the warm-up
  run's already-populated DB and the Streamlit UI. The story is identical.
- **Want the richest version?** Drop `--focus` to ingest infra sources too — but
  the answer gets more hedged (it weighs "downstream slow" vs "webapp exhausted"),
  which is realistic but harder to narrate in 5 minutes. Keep `--focus` for clarity.
- **Strongest possible version (if you have 10 min):** run once WITHOUT the Mule
  file (vague "probable cause"), then WITH it (definitive) — the before/after flip
  is the most convincing thing you can show. See MULE_INTEGRATION.md Section 6.
