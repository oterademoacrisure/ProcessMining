# RCA Precedent Memory — "Have we seen this before?"

*A simple overview for the team.*

## What it does (in one line)
When a new incident is detected, the system looks through our history of past
incidents, finds the most similar ones, and shows the AI **how they were fixed —
and whether those fixes actually worked** — so the diagnosis is grounded in real
experience, not guesswork.

---

## The end-to-end flow

```
1. DETECT     Logs from all sources (Appian, Mule, Prometheus, Fluentd) flagged
                    │
2. DIAGNOSE   The AI analyzes the incident. Before finalizing, it pulls in
              SIMILAR PAST INCIDENTS (precedent) + how they were resolved
                    │
3. TICKET     A ServiceNow ticket is created to track the incident
                    │
4. APPROVE    An engineer reviews the suggested fix and approves it  ← the only human step
                    │
5. FIX + VERIFY   The fix runs and is automatically verified
                    │
6. LEARN      The resolved incident is saved back into memory, tagged with whether
              the fix WORKED (approved) or NOT (rejected). Next time a similar
              problem appears, this lesson shows up in step 2.
```

The key idea: **step 6 feeds step 2.** Every incident we resolve makes the next
diagnosis smarter.

---

## How we find similar past incidents — "hybrid search"

We search **three ways at once**, because no single way catches everything:

| Way | What it matches | Example |
|---|---|---|
| **Keyword** | exact words | finds tickets mentioning `credit-bureau-prod`, `HTTP 503` |
| **Meaning (semantic)** | the *meaning*, even with different words | "pool exhausted" ≈ "connection limit reached" |
| **Structural** | hard facts | same system, or the same recurring problem |

We then **combine** all three. Keyword is precise but misses synonyms; meaning
catches paraphrases but can blur exact IDs; structural anchors on hard facts.
Together they cover each other's blind spots.

> **Analogy:** finding a similar past medical case — you match by exact terms, by
> the *meaning* of the symptoms, and by which body system is affected. Using all
> three finds the right case more reliably than any one alone.

---

## How confident are we? (the score)

Each **clue** that a past incident is relevant is worth a confidence:

| Clue | Confidence it alone implies |
|---|---|
| Same recurring problem (exact fingerprint) | **95%** |
| Same system affected | **70%** |
| Same category | **30%** |
| Similar wording / meaning | **up to 60%** |

These are **judgment-based weights** — "how sure would I be if this were the only
clue?" We then **combine** whatever clues fired. The more clues agree, the higher
the confidence.

> **Analogy:** multiple witnesses identifying the same suspect. Each might be wrong
> on their own, but if several independently point to the same person, you're
> confident. We calculate the chance that *all the clues are wrong at once*, and
> whatever's left is our confidence.

**Worked example:** a past incident matches on **same system (70%)** *and* has
**decent wording overlap (50%)**:
- chance the system clue is misleading = 30%
- chance the wording clue is misleading = 50%
- chance **both** are misleading at once = 30% × 50% = **15%**
- so our confidence it's relevant = **85%**

Two medium clues stack to 85% — comfortably relevant. One weak clue alone (e.g.
category only) stays low and is dropped.

---

## The 60% bar (0.60)

**We only show the AI precedents we're at least 60% confident are truly relevant.**
Below 60%, we show nothing.

- 60% roughly means *"at least a same-system-level match."*
- **Better to show no precedent than a misleading one** — a loosely-related ticket
  would distract the AI more than help it.

---

## Proven vs failed (the outcomes)

Every precedent is labeled with what happened to its fix:

| Label | Meaning | How the AI uses it |
|---|---|---|
| ✅ **Approved** | the fix was applied and **verified to work** | a **proven fix — prefer it** |
| ❌ **Rejected** | the fix was tried and **did NOT work** | a **dead end — don't repeat it** |
| ⬜ **Unknown** | outcome not recorded | a hint, not proof |

Both matter: **approved** tells the AI what *to* do; **rejected** tells it what
*not* to do (so we don't repeat a known failure).

---

## Why it matters

- **Faster, more confident RCA** — grounded in real history, not guesses.
- **Gets smarter over time** — it learns from our *own* resolved incidents, not
  just imported ones.
- **Safe by design** — a strict 60% relevance bar, and a **human approves** every
  fix before anything runs.

---

## Under the hood (one line for the curious)

Past incidents' problem text is turned into a numeric "fingerprint" (an embedding)
and stored in a fast similarity index (FAISS). The fix, outcome, and details stay
in our database and are looked up when a match is found. The similarity engine is
**swappable** (FAISS today; can move to pgvector or Qdrant later) without changing
anything else.
