# RCA Precedent Memory — Complete Team Guide

*The full picture: the flow, every design choice and why, how scoring works, where
data lives, and how to inspect it. Written in plain language for the whole team.*

---

## 1. What it is & why it matters

When something breaks, the first questions are: **"Have we seen this before? How did
we fix it? Did that fix actually work?"** This system answers them automatically — it
**remembers past incidents and their resolutions** and feeds the **most relevant ones
into the AI's diagnosis.**

**Business value:**
- **Faster resolution** — proven fixes surface instantly.
- **Consistency** — same problem → same proven fix, whoever is on call.
- **Institutional memory** — knowledge survives team changes.
- **Avoids repeating mistakes** — we track what *didn't* work, not just what did.

---

## 2. The end-to-end flow

```
1. DETECT     Logs from all sources flagged
2. DIAGNOSE   AI analyzes — and pulls in SIMILAR PAST INCIDENTS + their fixes
3. TICKET     A ServiceNow ticket is CREATED (to track the incident)
4. APPROVE    An engineer reviews & approves the recommended fix   ← the only human step
5. FIX+VERIFY The fix runs and is verified
6. LEARN      The resolved incident is saved back as precedent, tagged worked/failed
```

**Two lifecycles, deliberately offset:**
- **ServiceNow ticket** → **created at DIAGNOSIS** (step 2), **resolved at VERIFY** (step 5).
- **Precedent (saved to memory)** → created **only at VERIFY/resolution** — because an
  incident only becomes a *lesson* once it's solved with a known outcome.

**Step 6 feeds Step 2** — every incident we resolve makes the next diagnosis smarter.

---

## 3. How we find similar incidents — "hybrid search"

We search **three ways** and combine them, because no single way is enough:

| Method | Matches on | Strength | Blind spot | Is it "text"? |
|---|---|---|---|---|
| **Keyword** | exact words | precise IDs, error codes | misses synonyms | ✅ text |
| **Semantic** | meaning | paraphrases / different words | can blur exact IDs | ✅ text |
| **Structural** | hard facts (same system, recurrence, category) | reliable anchor | needs the fact recorded | ❌ fact, not text |

**Two of the three are "text" methods** (keyword + semantic) — they both read the
problem *description*, just differently (literal words vs meaning). The **third
(structural)** matches database *facts*, not text.

**Why all three:** each covers the others' blind spots. This is the industry-standard
"hybrid search" approach and is far more robust than any single method.

---

## 4. Where semantic search specifically helps ⭐

This is the key reason we added "meaning" search. **It catches the same problem
described in different words** — which keyword search misses entirely:

> New incident: *"database connections maxed out"*
> Past incident: *"Hikari **pool exhausted**"*
> → **no shared words**, but the **same problem.** Keyword finds nothing; semantic
> recognizes they mean the same thing.

It also helps when an operator describes an issue **loosely/informally** — semantic
still maps it to the right structured past incident. (Keyword still wins on exact IDs
like `credit-bureau-prod` / `HTTP 503` — which is why we keep **both**.)

---

## 5. Where the data is stored (two stores, each doing what it's best at)

| Store | Holds | Why |
|---|---|---|
| **FAISS** (vector index) | **only** the numeric "fingerprint" (embedding) of each past *problem* + its `incident_id` + tenant | built for **fast similarity search** (find look-alikes in milliseconds) |
| **PostgreSQL** (database) | **everything else**: problem text, **the fix**, **the outcome**, system, dates, ticket number | built for **reliable storage & querying** |

**What's in FAISS, exactly:** `incident_id → [1536 numbers] + {tenant_id}`. **Nothing
else.** Not the fix, not the outcome, not the text.

**Why split:** FAISS answers *"which past problems are similar?"* (returns ids);
Postgres answers *"what were they and how were they fixed?"* by id. We deliberately
**don't** copy text/fix into FAISS — that would duplicate data and risk it going stale.

```
new problem → FAISS returns similar ids → Postgres provides the fix + outcome for those ids
```

**Note:** the **fix/solution is intentionally NOT in FAISS** — because (a) we match on
the *problem*, not the fix (a new incident has no fix yet), and (b) it already lives in
Postgres.

---

## 6. The embedding model

**Azure `text-embedding-3-small`** turns each problem's text into the 1536-number
fingerprint. **Why:** best quality/cost balance at our scale, runs on our **existing
Azure OpenAI**, and is reachable through **corporate security** (it's a standard HTTPS
service — unlike a database port, which the corporate proxy blocks).

---

## 7. How scoring works — two stages

We turn matches into a single **relevance %** in **two stages**. They solve different
problems, so we need both.

### Stage 1 — RRF: merge the two TEXT methods into one text score
Keyword and semantic each produce a ranked list, but their scores are on **different
scales** (you can't just add them). **Reciprocal Rank Fusion (RRF)** merges them by
**rank position** ("who placed 1st/2nd in each list"), which *is* comparable — and it
**rewards incidents both methods agree on.** Output: **one text score.**

### Stage 2 — noisy-OR: blend the text score with the FACT clues into one %
Now we combine **different kinds** of evidence — the text score (from Stage 1) **plus
the structural facts** — each carrying a confidence:

| Clue | Confidence it alone implies |
|---|---|
| Same recurring problem (exact fingerprint) | **95%** |
| Same system affected | **70%** |
| Same category | **30%** |
| Similar wording/meaning (the RRF text score) | **up to 60%** |

**How "noisy-OR" combines them (plain):** we compute the chance that *all the clues are
wrong at once*, and whatever's left is our confidence. **More clues agreeing → higher
confidence.** *(Like several witnesses independently identifying the same suspect.)*

> **Worked example:** same system (70%) + decent text (50%):
> chance both are misleading = 30% × 50% = 15% → **confidence = 85%.**

### Why BOTH stages are needed
- **RRF (Stage 1)** combines things of the **same kind** (two text methods, mismatched
  scales) → one fair text score. It can't produce an explainable % or include facts.
- **noisy-OR (Stage 2)** combines things of **different kinds** (text + facts) → one
  explainable %, and weights **hard facts above wording.**

```
keyword  ┐
         ├─ Stage 1: RRF → ONE text score ┐
semantic ┘                                │
                                          ├─ Stage 2: noisy-OR → relevance %  → 60% bar
same system / recurrence / category ──────┘  (the fact clues)
```

**Why this design (vs a black-box ML score):** it produces **one explainable number**
("82% relevant") and lets us **weight facts above words** — explainability matters for
trust and tuning.

---

## 8. The 60% bar

**Only precedents we're ≥ 60% confident are relevant reach the AI.** Below that, nothing.

| Bar | What it takes to qualify |
|---|---|
| 0.50 | loose — weak combos qualify (more noise) |
| **0.60 (current)** | **same system, OR strong dual-arm text, OR a recurrence** |
| 0.70 | stricter — must share the system |
| 0.80 | tight — system + text, or a recurrence |

**Why 0.60:** it's the "at least a same-system match" line — balanced and meaningful.
**It's a tunable setting** (`history_min_confidence`): raise it if weak matches leak in,
lower it if good ones are missed. *Better to show nothing than something misleading.*

---

## 9. Proven vs failed (approved / rejected / unknown)

Every precedent is labeled with what happened to its fix:

| Label | Meaning | How the AI uses it |
|---|---|---|
| ✅ **Approved** | fix applied and **verified to work** | a **proven fix — prefer it** |
| ❌ **Rejected** | fix **tried, did NOT work** | a **dead end — don't repeat it** |
| ⬜ **Unknown** | outcome not recorded | a hint, not proof |

**Crucial: the outcome does NOT affect the relevance score.** Scoring only asks *"is it
similar?"* — it's **outcome-blind.** A highly-similar **rejected** incident still scores
high and still surfaces.

**Where the outcome *does* matter:**
1. **Label shown to the AI** (the main place) — with "prefer proven, avoid failed".
2. **Ordering** — but only as a **tiebreaker** (see §10).
3. **The UI badge** the operator sees.

**Why kept separate from scoring:** if we *penalized* rejected ones in the score, a
highly-relevant failed fix could drop **below 60% and disappear** — and then the AI
would **never learn it already failed** and might **re-recommend the dead end.** Keeping
relevance and outcome separate is what lets us say *"this is similar AND this fix already
failed — avoid it."*

---

## 10. Ordering & ties

The qualifying precedents (≥60%) are sorted, then we keep the **top 3**:
1. **Confidence (highest first)** — the real ranking.
2. **Then approved-first** — *only when confidence is exactly equal.*
3. **Then most recent** — *only when confidence AND outcome are equal.*

**Exact ties are now rare** because RRF gives finely-separated text scores (e.g. 0.877
vs 0.874, not three identical 0.880s). A true tie mostly happens for **structural-only
matches** (e.g. two incidents on the same system, neither matching the text → both land
on exactly 0.70). Then the tiebreaker shows the **proven** one first.

---

## 11. What the AI actually receives

For each of the **top 3** precedents (all ≥60%), the AI gets:
```json
{ "ticket": "INC0009999", "system": "credit-bureau-prod",
  "what": "<the problem>", "resolution": "<the fix>",
  "outcome": "approved", "confidence": 0.99, "resolved_at": "2026-04-15" }
```
…plus an instruction to **prefer approved fixes, avoid rejected ones**, and to **name
the ticket** in its summary if it closely matches. (The embedding/vector and the scoring
math are **never** shown to the AI.)

---

## 12. The learning loop (how our own incidents become precedent)

When a remediation **verifies**, the resolved incident is **saved back as precedent**:
- **verified → approved**, **failed/unverified → rejected**.
- It's written to Postgres **and** indexed into FAISS, reusing the **real ServiceNow
  ticket id** so it links to the ticket and **never duplicates**.

There are **two sources** of precedent (by design):
- **Our own resolved incidents** → captured **directly and instantly** at verify.
- **External / past ServiceNow incidents** (other teams', historical) → pulled in by a
  scheduled **importer** (the only way to learn ones we never handled).

---

## 13. Why no separate "reranker" AI

A reranker is an extra model that re-sorts the shortlist. **We don't use one** because
our shortlist is tiny (top 3) and the **main diagnosis AI already judges those 3.** A
reranker would add cost/complexity for no real benefit. Kept as a future option *if* the
history grows very large.

---

## 14. Swappable engine (no lock-in)

The similarity engine is **swappable by one config setting** (`vector_backend`):
- **FAISS** (today) — runs **locally, zero extra infrastructure**; ideal given corporate
  network restrictions.
- **pgvector** or **Qdrant** (later) — managed vector databases (Qdrant even ships a web
  dashboard). Switch **without rewriting anything else.**

---

## 15. Safety & governance (built in)

- **A human approves every fix** before anything runs.
- **A strict 60% bar** keeps weak/misleading precedents out of the AI's view.
- **We record failures, not just successes** — never re-recommend a known dead end.
- **Full audit trail** — every incident gets a ServiceNow ticket; every executed command
  + outcome is stored.

---

## 16. How to inspect it (for engineers)

- **Files on disk:** `serverops/.data/precedent.faiss` (the index) + `….meta.json` (the
  id→tenant map).
- **Inspector script:** `python scripts/inspect_vector_index.py` (lists indexed incidents
  with outcome; `--query "..."` runs a test search).
- **In the app:** the Streamlit **"Precedent Memory"** page — browse + search the index
  with outcome badges.

---

## 17. All decisions at a glance

| # | Decision | Choice | Why |
|---|---|---|---|
| A | Learn from history | precedent retrieval | ground the AI in real experience |
| B | Search strategy | **hybrid** (keyword + semantic + structural) | each covers the others' blind spots |
| C | Semantic's role | same problem, different words | the biggest quality upgrade |
| D | Storage split | FAISS = fingerprints, Postgres = data | each tool at its best; no stale duplication |
| E | Embedding model | Azure `text-embedding-3-small` | quality/cost, reuses Azure, firewall-friendly |
| F | Text fusion (Stage 1) | Reciprocal Rank Fusion (RRF) | merges two mismatched scales fairly; rewards agreement |
| G | Scoring (Stage 2) | weighted clues + noisy-OR → one % | explainable; facts weighted above words |
| H | Confidence bar | 60% (tunable) | no misleading precedents; ≈ same-system match |
| I | Outcomes | approved/rejected/unknown, **not in scoring** | learn from results; never hide a useful "what-not-to-do" |
| J | Ordering | confidence → approved-first → recent | relevance leads; proven-first only on ties |
| K | Reranker | none (for now) | unnecessary at our scale; the main AI judges |
| L | Engine | FAISS default, swappable | no lock-in; zero infra now, scale later |

---

## 18. The one-paragraph summary

When an incident is detected, we find the most relevant *past* incidents using a
**hybrid search** — exact **keywords**, **meaning** (semantic), and **hard facts** (same
system / recurrence). The two *text* methods are merged with **RRF**; that text score is
then blended with the fact-based clues via **noisy-OR** into a **single explainable
relevance %** (facts weighted above wording). We keep only matches **≥ 60% confident**
and hand the AI the **top 3 — each tagged with whether its fix worked or failed.**
Crucially, the outcome **doesn't change the relevance score** (so we never hide a useful
"already-failed" case) — it tells the AI **what to do** with each match. The fast
look-alike search lives in **FAISS**; all real detail lives in **PostgreSQL**. Every
resolved incident **feeds back in**, so the system keeps getting smarter — safely, with a
**human approving every fix.**
