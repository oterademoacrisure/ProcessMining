"""POINT 19 (Task #19): (re)build the precedent vector index from historical_incident.

Usage:
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\build_vector_index.py [--tenant 1]

Embeds each past incident's problem text with the configured embedder and loads
the active vector backend (FAISS by default). Re-run after syncing new history or
changing the embedding model/dimension. (Config is wired into modules.yaml in
Step 5; for now it uses sensible defaults.)
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))
sys.path.insert(0, ROOT)

import yaml

from app.db.session import SessionLocal
from app.paths import PROJECT_ROOT
from app.servicenow_history.indexer import build_index
from app.vectorstore import Embedder, get_vector_store


def _vector_cfg() -> dict:
    """Use the SAME vector config as the investigator (modules.yaml llm_rca)."""
    with open(PROJECT_ROOT / "config" / "modules.yaml", encoding="utf-8") as f:
        m = yaml.safe_load(f)
    return (m.get("investigators", {}).get("llm_rca", {}).get("config", {})) or {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the precedent vector index (Task #19)")
    ap.add_argument("--tenant", type=int, default=None, help="limit to one tenant (default: all)")
    args = ap.parse_args()

    cfg = _vector_cfg()
    store = get_vector_store(cfg)
    embedder = Embedder(cfg)
    print("vector backend:", cfg.get("vector_backend", "faiss"), "| embedder mode:", embedder.mode)

    with SessionLocal() as s:
        n = build_index(s, store, embedder, tenant_id=args.tenant)
    print(f"indexed {n} incidents into the {cfg.get('vector_backend','faiss')} backend (store count={store.count()})")


if __name__ == "__main__":
    main()
