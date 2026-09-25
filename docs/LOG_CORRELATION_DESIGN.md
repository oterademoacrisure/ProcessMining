# Log Correlation — Final Solution Design

_How serverops turns logs from many sources into ONE logical incident, so the LLM can reason about a
single coherent context instead of scattered lines._

---

## 1. Goal
Given logs streaming from heterogeneous sources (Appian, MuleSoft, infrastructure, …), group the
related ones into a single **incident** — reliably, explainably, and even when the sources **do not
share a common id**.

## 2. Guiding principle (important correction)
**We correlate on what the source logs actually carry — not on an id we inject upstream.**

- A shared **trace/correlation id** is the ideal, but it only exists if the **source app emits it**.
  We usually do **not** control those apps, so a global trace id is **opportunistic, not a pillar**.
- The **OpenTelemetry** we add (Task #21) instruments **our own pipeline** (ingest→…→learn) for *our*
  observability. It does **not** place a trace id into the source logs. Different scope — don't conflate.
- Therefore the backbone is: **extract the entities the source logs already contain** (business ids,
  host, endpoint) + **probabilistic signals** when even those don't overlap.

## 3. Scenarios covered
| Scenario | How we correlate |
|---|---|
| Sources share a trace/correlation id | Exact match — deterministic (opportunistic) |
| No formal id, but share an entity in the text (order #, process id, host, endpoint) | **Extract the entity → deterministic match** |
| No shared key at all | **Probabilistic** — time + topology + meaning + learned history |
| Cross-tier (A calls B: Appian→Mule→infra) | Service-dependency (topology) link |

> Proof it works today: the **Mule reader maps `appian_process_id` → `case_reference_id`** under the
> same `process_definition_name` as the Appian trace — a **shared business entity extracted at ingest**.
> That (not any trace id) is what merges Mule + Appian into one incident. We generalize this pattern.

## 4. What exists today
- **Normalize at ingest** — each reader maps its source format into a common `event_log` schema with
  `server_id` / `case_id`.
- **`incident_grouper`** — **union-find** linking findings that share `server_id` **or** `case_id`
  **and** have overlapping time windows.
- ✅ Precise & explainable when sources share those keys.
- ❌ **Binary** (linked / not) and **blind** when no key is shared — no topology, meaning, or learning.

## 5. What improves (and how)
1. **Entity extraction / templating at ingest** (regex → SLM as volume grows): pull host, IP, endpoint,
   and **business keys** out of raw text → more deterministic joins for free (generalizes the
   `appian_process_id` trick to more fields/sources).
2. **Union-find → weighted graph** (`networkx`, in-memory): every link is a **scored edge**, not yes/no.

   | Edge signal | Weight | Source |
   |---|---|---|
   | shared id / entity | 1.0 (strong) | extracted keys / opportunistic trace id |
   | topology (A calls B) | 0.7 | Postgres `service_edges` table |
   | time proximity | 0.4 | co-occurrence window |
   | semantic similarity | 0.5 | FAISS embedder (reused from Task #19) |
   | learned co-occurrence | var | history: "these patterns spike together" |

3. **Grouping** = connected components / community detection above a **threshold**.
4. **Guardrail** — require **≥2 corroborating signals** when there is no strong id (avoids
   over-correlating on time alone).
5. **Explainable & self-tuning** — the score is surfaced; the **approved/rejected feedback loop** tunes
   the edge weights over time.
6. **LLM reasons on the grouped evidence** (existing `LlmRcaInvestigator`) — reasoning, not joining.

## 6. Technical flow (simple)
```
Ingest
  → Normalize + extract entities (+ templating)           more join keys
  → Build candidate edges between findings:
        shared id/entity · time · topology · semantic · learned co-occurrence
  → Weighted graph (networkx, in-memory)
  → Threshold + group (connected components / community)   = the incident
  → Anomaly rank (which cluster is the real issue)
  → LLM RCA on the incident (+ precedents)                 existing
  → Feedback tunes the edge weights                        gets smarter
```

## 7. Tooling — no graph database needed
The correlation graph is **tiny and ephemeral** (only the findings in one time window), so it's an
in-memory computation, not a stored graph.

| Use | Tool | New infra? |
|---|---|---|
| Correlation compute (group per cycle) | **`networkx`** in-memory (or extend union-find) | No |
| Service topology (soft-edge source) | **Postgres table** `service_edges` (already on Neon) | No |
| Semantic edges | **FAISS** embedder (reused) | No |
| Long-term incident knowledge graph (optional, later) | graph DB (Neo4j/Memgraph) or Apache AGE | Only if a real query workload appears |

A dedicated graph DB is **over-engineering** for per-incident correlation (new stateful service; Neon
can't host the AGE extension). Defer until a persistent, richly-queried knowledge graph is justified.

## 8. Existing vs improved — at a glance
| | Today | Improved |
|---|---|---|
| Link logic | hard union-find | weighted graph (hard + soft edges) |
| Signals | shared key + time | + entity extraction, topology, semantic, learned |
| No-shared-key case | missed | probabilistic score (≥2 signals) |
| Explainability | yes (key match) | yes (shown score) |
| Learning | none | feedback tunes weights |
| Infra | Postgres | + `networkx` (in-mem), reuse FAISS, Postgres topology table — no graph DB |

## 9. Where it lands in the codebase
- `app/readers/*` + a new **entity-extraction** step in ingest (or `persistence`) → populate extra keys.
- `app/orchestrator/incident_grouper.py` → evolve union-find into the **weighted-graph grouper**
  (hard edges = shared key/entity; soft edges = time, topology, semantic, learned).
- **Reuse** `app/vectorstore` embedder for the semantic edge.
- New small table `service_edges` (topology) via an Alembic migration.
- Feedback: reuse the approved/rejected signal to tune edge weights.

## 10. Phased plan
1. **Entity extraction at ingest** — biggest deterministic win; more logs join by fact.
2. **Weighted-graph grouper** (`networkx`) with time + topology + semantic soft edges + threshold + score.
3. **Learned co-occurrence** + **feedback-tuned weights** (self-improving).
4. **Opportunistic trace id** — extract it where a source already provides it / at boundaries we own.
5. _(Optional, later)_ persistent knowledge graph if an exploratory query workload emerges.

## 11. One-liner
**Keep the deterministic key/time backbone; extract the entities the source logs already carry so more
of them join by fact; and where nothing is shared, correlate probabilistically on time + topology +
meaning + learned history — as an in-memory weighted graph, explained by a score and tuned by feedback.
The LLM reasons on the result. Shared trace ids are a bonus where a source provides them — never a
dependency. No graph DB required.**
