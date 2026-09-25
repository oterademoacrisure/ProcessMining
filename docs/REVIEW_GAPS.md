# ServerOps — Architecture & Business Review: Gaps & Roadmap

_Reviewer lens: business reviewer + senior AI architect. Captured 2026-06-30 to execute later._

Overall: strong, well-architected closed loop (detect → diagnose-from-precedent → human-approve →
remediate → audit → learn), open-standard MCP integration, instrument-once observability, fail-safe
discipline. The gaps below are what stands between "impressive demo" and "production-trustable".

---

## 🔴 Headline gap — security + governance (do before any real execution)
The **untrusted-input → LLM → executable-command** path needs hardening:

1. **Logs are attacker-influenceable** and flow into the LLM prompt; the LLM's output becomes a
   *recommended command* that can be executed → prompt-injection → remediation vector.
2. **Approvals are unauthenticated** (Streamlit approver is free-text `anonymous`). Approval is the
   safety gate for running commands — must be authenticated, role-restricted, audited.
3. **Allow-list is verb-scoped, not resource-scoped** (permits "restart" but not *which*
   deployment/namespace/cluster).

**Fix:** SSO/RBAC on the UI + real approver identity in the audit trail · resource-scoped allow-list ·
redact secrets/PII from logs BEFORE the LLM and BEFORE telemetry export · validate LLM-proposed
commands against a resource allow-list.

---

## Technical gaps (AI architect)
| Area | Gap | Recommendation |
|---|---|---|
| RCA quality | No systematic eval; confidence self-reported; thin tests (severity-mismatch timeline bug slipped through) | Golden-incident eval set + Langfuse scoring; track diagnosis-accuracy & fix-success; optional 2nd "critic" LLM for high-impact actions |
| Observability scale | Live Activity is polling (DB query per viewer per 1–2s; new session each refresh) | Deferred **SSE + Redis** path (already designed) for prod |
| Retention | `pipeline_event` has `retention_hours` config but **no purge job** (unbounded growth) | Add periodic purge |
| Data privacy | Prompts (incident data) → Langfuse Cloud (US) = residency/PII risk | Self-host Langfuse (one-line `LANGFUSE_HOST`) and/or PII scrubbing |
| Cost control | No rate-limit/budget on LLM calls; incident storm = cost spike | Per-tenant rate limits, cost caps, cache/dedup near-identical diagnoses |
| Concurrency/scale | Single-process cyclic runner; multi-worker safety (locking, double-processing) unproven | Define scaling model before horizontal scale-out |
| Real execution | `executor_mode: real` runs subprocesses; blast radius / least-priv / rollback / change-windows underspecified | Sandbox + least-priv identity, mandatory dry-run, auto-rollback on failed verify, maintenance-window gating |
| Prompt/model versioning | Prompt changes silently alter RCA | Version prompts (Langfuse prompt mgmt); model id already captured from response |

## Business gaps (business reviewer)
| Area | Gap | Recommendation |
|---|---|---|
| ROI invisible | Data exists but no KPI dashboard (MTTR, % auto-resolved, precedent hit-rate, approval rate, cost/incident) | Small exec dashboard off `pipeline_event` + Langfuse — funds/renews the project |
| Trust / autonomy ladder | Always full HITL; no defined path to auto-remediation | Track per-action success rate; stages: advise-only → approve → auto for proven low-risk fixes |
| Coverage | Limited sources + restart-only remediation | Roadmap more remediation types + sources; report coverage as a KPI |
| Compliance evidence | Jira↔ServiceNow cross-link is strong, but unauthenticated approvals weaken it for SOC2/change-mgmt | Authenticated approver identity closes the audit story |

## Strengths to keep/leverage
Closed-loop self-improvement (precedent feedback) · open-standard MCP (swappable backend, no lock-in) ·
instrument-once observability (timeline + OTel + Langfuse from one hook) · fail-safe telemetry ·
cross-system audit linkage · graceful LLM fallback to evidence-only.

---

## Prioritized roadmap
**Now (governance/safety — before any real execution):**
1. Authenticate + RBAC the approval flow (real identity in the audit trail). **← highest-risk gap**
2. Resource-scoped allow-list + PII/secret redaction (before LLM and telemetry).
3. `pipeline_event` retention purge.

**Next (prove value & quality):**
4. Business KPI dashboard (MTTR, automation rate, precedent hit-rate, cost).
5. RCA eval harness (golden incidents + Langfuse scoring; regression tests incl. the correlation-key invariant).

**Strategic (scale & autonomy):**
6. SSE + Redis for the live view at scale.
7. Autonomy ladder backed by success-rate tracking; rollback + change-window hardening for real execution.
8. Self-host Langfuse (data residency) for enterprise rollout.
