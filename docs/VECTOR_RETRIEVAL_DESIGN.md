# Vector Retrieval Design — Decision Record

**Scope:** ServiceNow precedent retrieval (Task #19) — choosing the vector store, the
human-in-the-loop (HITL) feedback mechanism, the chunking strategy, and a retrieval
strategy that scales under high data load.

**Status:** Decided — `pgvector`. Pending one prerequisite (extension availability on the
target Postgres). See §1 and §8.

**Date:** 2026-06-24

---

## 0. TL;DR (the decision)

- **Store:** Use **pgvector** (a PostgreSQL extension), **not raw FAISS**. The precedent
  data and its metadata already live in Postgres, and retrieval *must* filter on that
  metadata (tenant, affected system, the recency guard, HITL feedback). pgvector does
  **vector search + metadata filter + transaction in one store**; FAISS is an in-memory
  *library* with none of that.
- **Reframe:** FAISS is a **library**, pgvector is a **DB feature** — not apples-to-apples.
  If we ever outgrow pgvector, the production move is a **managed vector DB
  (Qdrant / Milvus / Weaviate)**, *not* hand-rolled FAISS. (Milvus wraps FAISS and adds the
  database layer we would otherwise have to build.)
- **HITL:** Store the human approve/reject outcome **on the same row as the embedding** and
  use it as a **re-rank boost (proven fix) / penalty (rejected fix)** — trivial in pgvector,
  a parallel store in FAISS.
- **Chunking:** **Field-aware** — embed the *symptom* (`short_description + description`) as
  the match vector; keep the *resolution* (`close_notes`) as payload. Split only when a
  description exceeds ~512 tokens.
- **Retrieval:** **Two-stage hybrid** — vector ANN + keyword (FTS) + structural filters fused
  with Reciprocal Rank Fusion (RRF), then a rerank that applies the HITL feedback and the
  recency tiebreak.
- **Prerequisite:** pgvector needs the extension enabled. Our current dev Postgres reported
  it **unavailable**; managed Postgres (Neon, RDS/Aurora, Azure Postgres Flexible, AlloyDB)
  all support it. Confirm/enable, or host the precedent store on one that does.

---

## 1. Context & framing

Today the precedent retrieval (`app/servicenow_history/retrieval.py`) uses Postgres
full-text search (a generated `search_tsv` tsvector + GIN index) plus structural scoring
(system match, category, recency guard) combined with a noisy-OR confidence and a 0.60 floor.
That is **lexical** matching — it misses semantically similar incidents worded differently.
The goal of this revision is to add **semantic** (vector) retrieval and decide where the
vectors live.

**Important framing:** FAISS and pgvector are not the same kind of thing.

| | FAISS | pgvector |
|---|---|---|
| What it is | an in-memory ANN **library** | a PostgreSQL **extension** (vector column + ANN index) |
| Comes with persistence / backup / HA? | no — you build it | yes — it's Postgres |
| Metadata filtering | none (separate store + `IDSelector`/post-filter) | native SQL `WHERE` in the same query |
| Transactions / consistency with relational data | none | ACID, same row |

So the honest production comparison is **pgvector vs a managed vector database**. Raw FAISS is
only the right answer when you are building your own vector service at extreme scale — which is
not this use case.

---

## 2. Indexing

| | pgvector | FAISS |
|---|---|---|
| Index types | **HNSW** (graph; default, no training, strong recall/latency) and **IVFFlat** (needs training + `lists` tuning) | **Flat** (exact), **IVF**, **HNSW**, **PQ / IVFPQ** (compression for billion-scale), composable |
| Distance ops | L2, cosine, inner product | L2, IP, cosine + more |
| Filtered search | vector + `WHERE tenant/cmdb_ci/...` in one SQL; filtered-ANN recall improved by **partial indexes per tenant** or **iterative index scan** (pgvector ≥ 0.8) | pure vector only; filtering via `IDSelector` / post-filter against a **separate** metadata store |
| Updates / deletes | ordinary SQL; HNSW supports incremental add | incremental add OK for some index types; **deletes/rebuilds painful** for IVF/PQ |

**Verdict:** pgvector HNSW covers our needs with native metadata filtering. FAISS is more
tunable at extreme scale but offloads filtering and update handling onto us.

---

## 3. Scaling

| | pgvector | FAISS |
|---|---|---|
| Comfortable scale | ~**1–10M vectors** per table (HNSW, memory-resident index) | **10M–1B+**, GPU, PQ compression |
| Multi-tenant | partition / partial index by `tenant_id` | shard indexes yourself |
| Main bottleneck | index in RAM / shared buffers; build memory; filtered-ANN recall tuning | must fit RAM (or shard); rebuild cost |

**Reality for ServiceNow precedent:** resolved incidents accrue at *thousands to low-millions
per tenant over years* — not web-scale. That is **squarely inside pgvector's comfort zone**.
FAISS's billion-scale / GPU advantages are irrelevant here.

---

## 4. Production readiness

| | pgvector | FAISS |
|---|---|---|
| What it is | a DB extension | a library (no server, no persistence, no HA) |
| Persistence / backup / replication | **free** (it's Postgres) | you build it (save/load index files, snapshots) |
| Concurrency / transactions | ACID, MVCC | none — you serialize/guard writes |
| Sync with relational data | **none needed** (same row) | **dual-write / ETL** to keep FAISS ↔ Postgres consistent |
| Managed support | Neon, RDS/Aurora, Azure Flexible, AlloyDB, Supabase | none (DIY) — or use Milvus/Qdrant which wrap it |
| Ops burden | low (we already run Postgres) | high (a whole service to build & operate) |

**Verdict:** pgvector is production-ready *with our existing stack*. Raw FAISS means building
and operating a vector service — only justified at a scale we do not have.

---

## 5. Decision & rationale

**Choose pgvector**, decisively, for this use case:

1. **Filtered search is mandatory.** Retrieval must scope by `tenant_id`, affected system
   (`cmdb_ci`), the **recency guard** (`resolved_at <= incident_time`), and HITL feedback.
   pgvector does this in one SQL query; FAISS makes it a sync-and-post-filter problem.
2. **Volume is modest** — well within pgvector/HNSW limits; no need for FAISS machinery.
3. **Single source of truth + transactional + backed up + multi-tenant** — all free with
   Postgres.
4. **HITL feedback is trivial** to incorporate (more columns on the same row) — see §6.
5. **Escape hatch:** if we reach 10M+ vectors per tenant or need GPU, migrate to a **managed
   vector DB (Qdrant / Milvus / Weaviate)** — not raw FAISS.

**Prerequisite / risk:** pgvector requires `CREATE EXTENSION vector`. Our current dev Postgres
reported it unavailable. Managed Postgres offerings support it; confirm on the target
instance, or relocate the precedent store. **This gates the implementation** — if the
extension truly cannot be enabled and scale is large, re-evaluate a managed vector DB instead.

---

## 6. HITL (approve / reject) inside the vector store

The human decision — whether a precedent's recommended fix was **approved/applied** vs
**rejected/ineffective** — is a learning signal that should bias future retrieval. This ties
the Task #20 approval flow to the Task #19 precedent memory: approve/reject decisions become
training signal for ranking.

**Storage:** keep the feedback **on (or directly linked to) the precedent row** alongside the
embedding — e.g. `approved_count`, `rejected_count`, `last_outcome`. With pgvector the vector
and the feedback live on the same row, so the search and the feedback weighting happen in the
same query.

**Use in ranking** (not just storage) — the final score blends:

```
score = w1 * semantic_similarity   (vector ANN)        # pgvector
      + w2 * lexical_overlap        (FTS / BM25)        # keyword
      + w3 * structural_match       (tenant / system)   # filters
      + w4 * feedback_signal        (approved↑ / rejected↓)
```

A precedent whose fix was **approved & verified** is boosted ("proven"); one that was
**rejected / did not work** is penalized or filtered out.

**Why pgvector wins here:** feedback is columns we `JOIN` and weight in the same SQL as the
vector search. In FAISS we would maintain a *parallel* feedback store and post-process —
more moving parts and a consistency risk.

**Best search mechanism:** hybrid retrieval with a feedback-aware rerank (see §7).

---

## 7. Chunking strategy

ServiceNow incidents are **short documents** (a title, a few-sentence problem, a resolution),
so heavy chunking is unnecessary and usually harmful (it dilutes the match).

- **Field-aware embedding (recommended):** embed the **symptom** =
  `short_description + description` as the *match* vector (a new incident's symptoms search
  against past symptoms); keep **`close_notes` (the fix) as payload**, not as a match key.
  This aligns retrieval with intent: *match on the problem, return the proven fix.*
- **One vector per incident** in the common (short-text) case.
- **Chunk only long descriptions** (multi-paragraph logs, > ~512 tokens): split into
  ~256–512-token chunks with ~15% overlap, embed each, and attribute the best-matching chunk to
  its parent incident (max-pool). Most incidents will not need this.
- **Embedding model:** pick one and pin it (we have Azure OpenAI → `text-embedding-3-small`,
  1536-d, is a sensible default). **Store the model + version** so embeddings can be migrated on
  upgrade; **normalize** vectors for cosine distance.

---

## 8. Retrieval strategy that scales under high load

**Two-stage hybrid retrieval** — the standard scalable pattern:

1. **Recall (cheap, parallel):**
   - **Vector ANN** (pgvector HNSW) — semantic match.
   - **Lexical** (Postgres FTS / tsvector — *already built*) — exact terms (system names,
     error codes).
   - **Structural pre-filter** — `tenant_id`, `cmdb_ci` / category, **`resolved_at <=
     incident_time`** (recency guard).
   - Fuse the two ranked lists with **Reciprocal Rank Fusion (RRF)** → top ~50 candidates.
2. **Rerank (precise, small N):** a cross-encoder or LLM rerank over the ~50 → apply the
   **HITL feedback boost/penalty** and the **recency tiebreak** → top-k (3–5). Keep the
   existing **confidence floor (≥ 0.60)** on the fused score.

**To scale under load:**

- **HNSW** index; tune `m` / `ef_construction` (build) and `ef_search` (query) for the
  recall/latency trade-off.
- **Partition or partial-index by `tenant_id`** so each tenant's ANN search is small (this
  also fixes filtered-ANN recall loss).
- **Embed once on ingest** (never re-embed stored docs at query time); cache the query
  embedding.
- **Read replicas + connection pooling** for retrieval throughput; the write/sync path stays
  on the primary.
- Degrades gracefully: if the vector stage is cold, lexical + structural still return results.

---

## 9. Delta from what exists today

The current `retrieval.py` already has the FTS + structural scoring and a `rerank` seam. This
design **slots in**: add an `embedding` column (pgvector) + the ANN recall stage + RRF fusion
+ the feedback-weighted rerank. The structural/recency/fail-safe logic is retained.

| Component | Today | After |
|---|---|---|
| Lexical match | FTS (`search_tsv` + GIN) | kept, as one recall arm |
| Semantic match | — | pgvector HNSW (new) |
| Fusion | single noisy-OR score | RRF over lexical + vector, then rerank |
| HITL feedback | — | feedback columns → re-rank boost/penalty |
| Confidence floor | 0.60 | 0.60, on the fused score |

---

## 10. Open items / next steps

1. **pgvector prerequisite — resolved on Azure.** The local Windows Postgres 18.1 cannot
   install pgvector (extension not available, and a from-source build is blocked by group
   policy). Decision: host the precedent store on **Azure Database for PostgreSQL – Flexible
   Server**, where `vector` is a Microsoft-supported, allow-listed extension
   (`azure.extensions = VECTOR`). A server has been provisioned for this.
2. **Connectivity constraint (Zscaler).** Corporate **Zscaler blocks outbound TCP 5432**, so
   the dev laptop cannot reach the Azure DB directly (Azure firewall is correctly configured;
   the block is the egress proxy, which only allows web ports 80/443). Implications:
   - DB **admin** (e.g. `CREATE EXTENSION vector;`, migrations) is done via **Azure Cloud
     Shell** (browser, port 443).
   - **serverops must run inside Azure** (VM / App Service / Container, co-located with the
     DB) **or** obtain a **sanctioned Zscaler exception** for 5432. Tunnelling 5432 over an
     allowed port is circumventing a security control and is out of scope.
   - This reinforces §5: the deployment target is Azure-side, with the DB ideally on a
     private endpoint.
3. **Pick the embedding model** and pin its version (default: Azure `text-embedding-3-small`).
4. **Design the feedback schema** once, shared by Task #19 (precedent ranking) and Task #20
   (approval flow).
5. Optional: a fact-checked benchmark pass (pgvector vs Qdrant/Milvus, filtered-recall tuning)
   if hard numbers are needed before committing.
