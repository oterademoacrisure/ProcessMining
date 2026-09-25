"""POINT 19 (Task #19): hybrid precedent retrieval — "have we seen this before?"

Given a live incident's signals (identity, systems, category, problem text), find
the few most similar PAST incidents and return them with their resolution AND
outcome (approved / rejected / unknown).

HYBRID recall — three arms, unioned (each catches what the others miss):
  - semantic  : FAISS vector search on the problem text (meaning/paraphrase)
  - keyword   : Postgres full-text search (exact terms — system names, codes)
  - structural: exact correlation_id / cmdb_ci matches (recurrences never missed)

Confidence (0..1) — the same noisy-OR as before: each matched signal is
independent evidence; they combine as confidence = 1 - PRODUCT(1 - p):
  p = 0.95  exact recurrence (same correlation_id)
      0.70  same system (same cmdb_ci)
      0.30  same category
      ≤0.60 TEXT signal = Reciprocal Rank Fusion (RRF) of the two text arms
            (semantic + keyword), mapped to 0..0.60. Full 0.60 needs strong
            agreement across BOTH arms; a single-arm match is weaker (~0.30).
Threshold to keep: >= 0.60.

Outcome: approved / rejected / unknown is carried through and used for ordering
(approved first) and labeling in the LLM prompt. BOTH approved (proven, prefer)
and rejected (tried & failed, avoid) surface above 0.60 — different roles.

Recency is NOT part of confidence — it only breaks ties in ordering.

Guards: tenant-scoped; only past incidents (resolved at/before as_of); must hit
at least one REAL signal (not category-only); confidence < min_confidence ->
dropped. Fails safe: no decent match -> []. The investigator then simply has no
precedent block; nothing breaks.

Backward-compatible: if no vector store/embedder is supplied, the semantic arm is
skipped and retrieval falls back to keyword + structural only.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.db.models import HistoricalIncident
from app.vectorstore import Embedder, VectorStore


log = logging.getLogger(__name__)

# POINT 19: Reciprocal Rank Fusion dampening constant (standard default = 60).
RRF_K = 60
# Best-case fused score: ranked #1 in BOTH text arms. Used to normalize RRF -> 0..0.60.
_RRF_MAX = 2.0 / (RRF_K + 1)

# POINT 19: ordering preference by outcome — proven fix first, caution last.
_OUTCOME_RANK = {"approved": 2, "unknown": 1, "rejected": 0}


def _rrf_text_scores(cosine: dict[int, float], tsrank: dict[int, float]) -> dict[int, float]:
    """POINT 19: Reciprocal Rank Fusion of the two TEXT arms (semantic + keyword).

    Each arm ranks its own hits; a candidate's fused score = sum of 1/(RRF_K + rank)
    over the arms it appears in. Fusing by RANK (not raw score) sidesteps the
    cosine-vs-ts_rank scale mismatch, and rewards incidents ranked high in BOTH
    arms over those strong in only one.
    """
    fused: dict[int, float] = {}
    for arm in (cosine, tsrank):
        for rank, iid in enumerate(sorted(arm, key=lambda i: arm[i], reverse=True), start=1):
            fused[iid] = fused.get(iid, 0.0) + 1.0 / (RRF_K + rank)
    return fused


@dataclass
class Precedent:
    """One similar past incident + how it was fixed + whether the fix worked."""
    number: str | None
    sys_id: str | None
    short_description: str | None
    close_notes: str | None          # the resolution — the precedent payload
    cmdb_ci: str | None
    category: str | None
    resolved_at: datetime | None
    correlation_id: str | None
    confidence: float                # POINT 19: 0..1 relevance confidence (noisy-OR)
    outcome: str                     # POINT 19: approved | rejected | unknown


def find_precedents(
    session: Session,
    tenant_id: int,
    *,
    correlation_id: str | None = None,
    systems: list[str] | None = None,
    category: str | None = None,
    query_text: str | None = None,
    as_of: datetime | None = None,
    k: int = 3,
    min_confidence: float = 0.60,
    store: VectorStore | None = None,
    embedder: Embedder | None = None,
    rerank: Callable[[list[Precedent], str | None], list[Precedent]] | None = None,
) -> list[Precedent]:
    """Return up to `k` past incidents most similar to the given signals."""
    systems = [s for s in (systems or []) if s]
    pool = max(k * 5, 20)   # over-fetch per arm; we score everything and trim to k

    # --- recall arms: candidate incident_id -> per-arm signal ---
    cosine: dict[int, float] = {}
    if store is not None and embedder is not None and query_text:
        try:
            for hit in store.search(embedder.embed_one(query_text), k=pool, tenant_id=tenant_id):
                cosine[hit.id] = hit.score
        except Exception:
            log.exception("find_precedents: vector arm failed; continuing with keyword+structural")

    tsrank = _fts_scores(session, tenant_id, query_text, as_of, pool)
    structural_ids = _structural_ids(session, tenant_id, correlation_id, systems, as_of, pool)

    candidate_ids = set(cosine) | set(tsrank) | structural_ids
    if not candidate_ids:
        return []

    # POINT 19: fuse the two text arms by rank (RRF) — rewards agreement.
    rrf = _rrf_text_scores(cosine, tsrank)

    rows = {
        h.incident_id: h
        for h in session.scalars(
            select(HistoricalIncident).where(HistoricalIncident.incident_id.in_(candidate_ids))
        ).all()
    }

    # --- score each candidate with the noisy-OR confidence ---
    precedents: list[Precedent] = []
    for iid in candidate_ids:
        h = rows.get(iid)
        if h is None:
            continue
        # POINT 19: recency guard — only genuinely-past incidents count as precedent.
        if as_of is not None and h.resolved_at is not None and h.resolved_at > as_of:
            continue

        p_corr = 0.95 if (correlation_id and h.correlation_id == correlation_id) else 0.0
        p_sys  = 0.70 if (systems and h.cmdb_ci in systems) else 0.0
        p_cat  = 0.30 if (category and h.category == category) else 0.0
        # POINT 19: TEXT signal = RRF of the two text arms, mapped to 0..0.60.
        # Full 0.60 needs strong agreement across BOTH arms; a single-arm match
        # caps ~0.30 and needs a structural clue to clear the 0.60 bar.
        p_text = min(0.60, 0.60 * rrf.get(iid, 0.0) / _RRF_MAX)

        # POINT 19: must hit at least one REAL signal — never rank on category alone.
        if not (p_corr or p_sys or p_text):
            continue

        confidence = 1 - (1 - p_corr) * (1 - p_sys) * (1 - p_cat) * (1 - p_text)
        if confidence < min_confidence:
            continue
        precedents.append(_to_precedent(h, round(float(confidence), 3)))

    # POINT 19: order by confidence, then proven-fix-first, then most recent.
    precedents.sort(
        key=lambda p: (
            p.confidence,
            _OUTCOME_RANK.get(p.outcome, 1),
            p.resolved_at.timestamp() if p.resolved_at else 0.0,
        ),
        reverse=True,
    )
    precedents = precedents[:k]

    # POINT 19: Stage-2 rerank seam — drop in an LLM/cross-encoder reranker here
    # later to reorder candidates. Default: no rerank.
    if rerank is not None and precedents:
        precedents = rerank(precedents, query_text)

    log.info(
        "find_precedents[tenant=%d]: %d precedent(s) (arms: vec=%d fts=%d struct=%d)",
        tenant_id, len(precedents), len(cosine), len(tsrank), len(structural_ids),
    )
    return precedents


# ── recall arms ──────────────────────────────────────────────────────────────

def _fts_scores(
    session: Session, tenant_id: int, query_text: str | None, as_of: datetime | None, limit: int
) -> dict[int, float]:
    """Keyword arm: incident_id -> ts_rank for full-text matches."""
    tsq = _or_tsquery_input(query_text)
    if not tsq:
        return {}
    where = ["h.tenant_id = :tenant", "h.search_tsv @@ to_tsquery('english', :tsq)"]
    params: dict = {"tenant": tenant_id, "tsq": tsq, "limit": limit}
    if as_of is not None:
        where.append("(h.resolved_at IS NULL OR h.resolved_at <= :as_of)")
        params["as_of"] = as_of
    sql = (
        "SELECT h.incident_id, ts_rank(h.search_tsv, to_tsquery('english', :tsq)) AS r "
        f"FROM historical_incident h WHERE {' AND '.join(where)} ORDER BY r DESC LIMIT :limit"
    )
    rows = session.execute(text(sql), params).mappings().all()
    return {int(r["incident_id"]): float(r["r"]) for r in rows}


def _structural_ids(
    session: Session,
    tenant_id: int,
    correlation_id: str | None,
    systems: list[str],
    as_of: datetime | None,
    limit: int,
) -> set[int]:
    """Structural arm: ids matching exact correlation_id or cmdb_ci (recurrences /
    same-system) — so they surface even if the text doesn't match."""
    clauses: list[str] = []
    params: dict = {"tenant": tenant_id, "limit": limit}
    expanding: list[str] = []
    if correlation_id:
        clauses.append("h.correlation_id = :cid")
        params["cid"] = correlation_id
    if systems:
        clauses.append("h.cmdb_ci IN :systems")
        params["systems"] = systems
        expanding.append("systems")
    if not clauses:
        return set()
    where = ["h.tenant_id = :tenant", "(" + " OR ".join(clauses) + ")"]
    if as_of is not None:
        where.append("(h.resolved_at IS NULL OR h.resolved_at <= :as_of)")
        params["as_of"] = as_of
    sql = f"SELECT h.incident_id FROM historical_incident h WHERE {' AND '.join(where)} LIMIT :limit"
    stmt = text(sql)
    for name in expanding:
        stmt = stmt.bindparams(bindparam(name, expanding=True))
    return {int(r[0]) for r in session.execute(stmt, params).all()}


# ── helpers ──────────────────────────────────────────────────────────────────

def _to_precedent(h: HistoricalIncident, confidence: float) -> Precedent:
    return Precedent(
        number=h.number, sys_id=h.sys_id,
        short_description=h.short_description, close_notes=h.close_notes,
        cmdb_ci=h.cmdb_ci, category=h.category, resolved_at=h.resolved_at,
        correlation_id=h.correlation_id, confidence=confidence,
        outcome=h.outcome or "unknown",
    )


def _or_tsquery_input(query_text: str | None) -> str | None:
    """Turn free text into an OR-joined tsquery input ('credit | bureau | timeout').

    OR (not AND) so a past ticket matches on ANY shared important word. Tokens are
    alphanumeric only (safe for to_tsquery), length > 2, deduped, capped. Postgres'
    english config drops stopwords/stems during to_tsquery itself.
    """
    if not query_text:
        return None
    seen: set[str] = set()
    out: list[str] = []
    for tok in re.findall(r"[a-zA-Z0-9]+", query_text.lower()):
        if len(tok) <= 2 or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
        if len(out) >= 25:
            break
    return " | ".join(out) if out else None
