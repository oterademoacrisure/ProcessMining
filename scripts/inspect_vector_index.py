"""POINT 19 (Task #19): inspect the precedent vector index (FAISS or pgvector).

Shows the active backend + vector count, lists the indexed incidents joined to
their historical_incident row (outcome + short_description), and optionally runs a
test semantic search.

Usage:
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\inspect_vector_index.py
    ... --query "credit bureau timing out"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
sys.path.insert(0, ROOT)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from sqlalchemy import text

from app.db.models import HistoricalIncident
from app.db.session import SessionLocal
from app.paths import PROJECT_ROOT
from app.vectorstore import Embedder, get_vector_store


def _vector_cfg() -> dict:
    with open(PROJECT_ROOT / "config" / "modules.yaml", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    return (m.get("investigators", {}).get("llm_rca", {}).get("config", {})) or {}


def _indexed_ids(cfg: dict, tenant: int) -> list[int]:
    """Which incident_ids are indexed — from the FAISS sidecar, or from the DB
    (pgvector: rows whose embedding column is set)."""
    backend = str(cfg.get("vector_backend", "faiss")).lower()
    if backend == "pgvector":
        with SessionLocal() as s:
            rows = s.execute(text(
                "SELECT incident_id FROM historical_incident "
                "WHERE embedding IS NOT NULL AND tenant_id = :t ORDER BY incident_id"
            ), {"t": tenant}).all()
        return [int(r[0]) for r in rows]
    # faiss: read the meta sidecar
    path = Path(cfg.get("vector_index_path", ".data/precedent.faiss"))
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if not meta_path.exists():
        return []
    return sorted(int(k) for k in json.loads(meta_path.read_text(encoding="utf-8")).keys())


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect the precedent vector index (Task #19)")
    ap.add_argument("--query", default=None, help="run a semantic search with this text")
    ap.add_argument("--tenant", type=int, default=1)
    ap.add_argument("--k", type=int, default=5)
    args = ap.parse_args()

    cfg = _vector_cfg()
    store = get_vector_store(cfg)
    print("backend      :", cfg.get("vector_backend", "faiss"))
    print("vector count :", store.count())

    ids = _indexed_ids(cfg, args.tenant)
    if ids:
        with SessionLocal() as s:
            print(f"\nindexed incidents ({len(ids)}):  [id]  [outcome]  number  short_description")
            for iid in ids:
                h = s.get(HistoricalIncident, iid)
                if h:
                    print(f"  {iid:<4} [{(h.outcome or '?'):8}] {(h.number or '-'):<18} {(h.short_description or '')[:55]}")
                else:
                    print(f"  {iid:<4} (no Postgres row - STALE index entry)")

    if args.query:
        emb = Embedder(cfg)
        print(f"\nsearch: {args.query!r}  (embedder mode={emb.mode})")
        hits = store.search(emb.embed_one(args.query), k=args.k, tenant_id=args.tenant)
        with SessionLocal() as s:
            for hit in hits:
                h = s.get(HistoricalIncident, hit.id)
                desc = (h.short_description or "")[:50] if h else "(missing)"
                outcome = (h.outcome if h else "?")
                print(f"  score={hit.score:.3f}  id={hit.id:<4} [{outcome:8}] {desc}")


if __name__ == "__main__":
    main()
