# RCA Precedent Memory — Design Decisions & Rationale

*A business-level explanation of what we built, the choices we made, and why.*

---

## 1. The problem we're solving

When something breaks in production, the first questions are always:
**"Have we seen this before? How did we fix it last time? Did that fix actually work?"**

Answering those manually is slow and depends on who's on call. Our system
automates it: it **remembers past incidents and their resolutions**, and feeds the
**most relevant ones into the AI's diagnosis**.

**Business value:**
- **Faster resolution** — proven fixes surface instantly instead of being re-derived.
- **Consistency** — the same problem gets the same proven fix, regardless of who's on call.
- **Institutional memory** — knowledge survives team changes; it's not in someone's head.
- **Avoids repeating mistakes** — we track what *didn't* work, not just what did.

---

## 2. The end-to-end flow

```
1. DETECT     Logs from all sources are flagged
2. DIAGNOSE   The AI analyzes — and pulls in SIMILAR PAST INCIDENTS + their fixes
3. TICKET     A ServiceNow ticket is created (to track the incident)
4. APPROVE    An engineer reviews & approves the recommended fix   ← the only human step
5. FIX+VERIFY The fix runs and is verified
6. LEARN      The resolved incident is saved back as precedent, tagged worked / failed
```

**Step 6 feeds Step 2** — every incident we resolve makes the next diagnosis smarter.

---

## 3. Every decision, and why

### Decision A — Learn from history (precedent retrieval)
**Choice:** pull similar past incidents into the diagnosis, rather than letting the
AI reason from scratch each time.
**Why:** grounding the AI in *real, verified experience* is far more reliable than
guesswork — and it reuses hard-won knowledge instead of throwing it away.

### Decision B — Search three ways at once ("hybrid search")
**Choice:** find similar past incidents using **three** methods, then combine them:

| Method | What it matches | Strength | Blind spot |
|---|---|---|---|
| **Keyword** | exact words | precise IDs, error codes | misses synonyms |
| **Semantic (meaning)** | the *meaning*, even with different words | paraphrases | can blur exact IDs |
| **Structural** | hard facts (same system, recurrence) | reliable anchor | needs the fact recorded |

**Why all three:** each one alone has a blind spot the others cover. Using all three
is the **industry best practice** for search quality and is far more robust than any
single method.

### Decision C — Where semantic search specifically helps ⭐
This is the key reason we added the "meaning" method. **It catches the same problem
described in *different words*** — which keyword search completely misses:

> New incident: *"database connections maxed out"*
> Past incident: *"Hikari **pool exhausted**"*
> → **No shared keywords**, but the **same problem**. Keyword search finds nothing;
> semantic search recognizes they mean the same thing.

It also helps when an operator describes a new issue **loosely or informally** —
semantic still maps it to the right structured past incident. This is the single
biggest quality upgrade from adding embeddings.

### Decision D — Where data is stored (two stores, each doing what it's best at)

| Store | Holds | Why |
|---|---|---|
| **FAISS** (vector index) | only the numeric "fingerprint" (embedding) of each past *problem* + its id | built for **fast similarity search** — finds look-alikes in milliseconds |
| **PostgreSQL** (database) | everything human-meaningful: problem text, **the fix**, **the outcome**, system, dates | built for **reliable storage & querying** of real data |

**Why split them:** each tool does what it's best at. FAISS answers *"which past
problems are similar?"* (returns ids); Postgres answers *"what were they and how were
they fixed?"*. We deliberately **don't** copy the text/fix into FAISS — that would
duplicate data and risk it going stale.

### Decision E — The embedding model (Azure `text-embedding-3-small`)
**Choice:** Microsoft Azure's `text-embedding-3-small` to create the fingerprints.
**Why:** best **balance of quality and cost** at our scale; runs on our **existing
Azure OpenAI** subscription; and it's reachable through **corporate security**
(it's a standard secure web service), unlike a database which corporate firewalls block.

### Decision F — How we score relevance (clear, weighted clues)
**Choice:** each clue that a past incident is relevant carries a confidence, and we
combine them into a single **0–100% relevance score**:

| Clue | Confidence it alone implies |
|---|---|
| Same recurring problem (exact fingerprint) | 95% |
| Same system affected | 70% |
| Same category | 30% |
| Similar wording / meaning | up to 60% |

**Why this approach:** it produces **one explainable number** the business can read
("82% relevant"), and it lets us **weight hard facts (same system) above soft evidence
(matching words)**. A black-box machine-learning score would be just as fast but not
*explainable* — and explainability matters for trust and tuning.

> **How the combine works (plain):** we calculate the chance that *all the clues are
> wrong at once*, and whatever's left is our confidence. More clues agreeing → higher
> confidence. (Like several witnesses independently identifying the same suspect.)

### Decision G — Combining the two text signals: Reciprocal Rank Fusion (RRF)
**Choice:** when keyword and semantic both find an incident, merge their results with
**RRF** (a standard hybrid-search technique that combines by **rank position**).
**Why:** keyword and semantic scores are on **different scales** (apples vs oranges) —
you can't just add them. RRF sidesteps that by using rank, and it **rewards incidents
that *both* methods agree are relevant**. It's also the technique that scales if our
history grows large.

### Decision H — The 60% relevance bar
**Choice:** only precedents we're **≥ 60% confident** are relevant reach the AI.
**Why:** **better to show nothing than something misleading** — a loosely-related
incident distracts the AI more than it helps. 60% ≈ *"at least a same-system match."*
It's a **tunable setting** (raise it if weak matches leak in; lower it if good ones
are missed).

### Decision I — Tracking outcomes (the human-in-the-loop feedback loop)
**Choice:** every precedent is labeled with what happened to its fix —
**✅ Approved** (verified to work), **❌ Rejected** (tried, didn't work), or
**⬜ Unknown** — and **our own resolved incidents feed back in** as new precedent.
**Why:** the system **gets smarter from our own outcomes**. Approved precedents tell
the AI *what to do*; rejected ones tell it *what NOT to repeat*. Both are valuable —
knowing a dead end is as useful as knowing a fix.

### Decision J — No separate "reranker" AI (for now)
**Choice:** we did **not** add an extra ranking model.
**Why:** at our scale the shortlist is tiny (top 3), and the **main diagnosis AI
already weighs them**. A reranker would add cost and complexity for little gain. It's
noted as a future option *if* the history grows very large.

### Decision K — Swappable engine (FAISS now; pgvector / Qdrant later)
**Choice:** the similarity engine is **swappable by one configuration setting**.
**Why:** **no lock-in.** FAISS runs **locally with zero extra infrastructure** — ideal
now, especially given corporate network restrictions. If we outgrow it, we switch to a
managed vector database (**pgvector** or **Qdrant**, which even comes with its own
dashboard) **without rewriting anything else.**

---

## 4. Safety & governance (built in)

- **A human approves every fix** before anything runs (the only manual step).
- **A strict 60% bar** keeps weak/misleading precedents out of the AI's view.
- **We record failures, not just successes** — so we never re-recommend a known dead end.
- **Full audit trail** — every incident gets a ServiceNow ticket; every executed
  command and its outcome is stored.

---

## 5. All decisions at a glance

| # | Decision | Choice | Why (one line) |
|---|---|---|---|
| A | Learn from history | precedent retrieval | ground the AI in real experience, not guesses |
| B | Search strategy | **hybrid** (keyword + semantic + structural) | each method covers the others' blind spots |
| C | Semantic search role | catch same problem, different words | the biggest quality upgrade |
| D | Storage split | FAISS = fingerprints, Postgres = data | each tool does what it's best at |
| E | Embedding model | Azure `text-embedding-3-small` | quality/cost balance, reuses Azure, firewall-friendly |
| F | Scoring | weighted clues + noisy-OR → one % | explainable; hard facts weighted above words |
| G | Text fusion | Reciprocal Rank Fusion (RRF) | merges two different scales fairly; rewards agreement |
| H | Confidence bar | 60% (tunable) | no misleading precedents; ≈ same-system match |
| I | Outcomes | approved / rejected / unknown + feedback loop | learns from our own results, avoids repeat failures |
| J | Reranker | none (for now) | unnecessary at our scale; the main AI judges |
| K | Engine | FAISS default, swappable | no lock-in; zero infra now, scale later |

---

## 6. The one-paragraph summary

When an incident is detected, we find the most relevant *past* incidents using a
**hybrid search** — exact **keywords**, **meaning** (semantic), and **hard facts**
(same system / recurrence) — because no single method is enough on its own. The
"meaning" search is what lets us match the **same problem described in different
words**. We score each match into a **single explainable relevance %** (weighting
hard facts above wording), keep only those **≥ 60% confident**, and hand the AI the
top few **with whether their fix worked or failed**. The fast look-alike search lives
in **FAISS**; all the real detail lives in **PostgreSQL**. Every resolved incident
**feeds back in**, so the system keeps getting smarter — safely, with a **human
approving every fix.**
