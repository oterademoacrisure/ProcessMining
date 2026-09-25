"""POINT 19 (Task #19): text -> embedding vector.

Real mode: Azure OpenAI embeddings (reachable over 443/HTTPS — works through
Zscaler), using the deployment named in AZURE_OPENAI_EMBED_DEPLOYMENT.

Fallback mode: a deterministic hashed bag-of-words vector, so dev/plumbing runs
even with no embedding deployment configured. It gives crude lexical similarity
(shared words -> similar vectors) — enough to exercise the pipeline; the FTS arm
still does the real keyword matching in that mode.

IMPORTANT: rebuild the index if you switch modes/models — vectors from different
embedders are not comparable.
"""
from __future__ import annotations

import hashlib
import logging
import os

from dotenv import load_dotenv

log = logging.getLogger(__name__)


class Embedder:
    def __init__(self, config: dict | None = None):
        config = config or {}
        load_dotenv()
        # POINT 19: for text-embedding-3-* you can shorten the output vector
        # (Matryoshka) via `dimensions` — e.g. 3-large @ 1024 keeps quality high
        # but the FAISS index small. self.dim (the vector length) follows it.
        self.dimensions = config.get("embed_dimensions")
        self.dim = int(self.dimensions or config.get("embed_dim", 1536))
        self.deployment = config.get("embed_deployment") or os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT")
        # POINT 19: Azure OpenAI needs the AzureOpenAI client (api_version + azure_endpoint),
        # not the plain OpenAI(base_url) client. Endpoint is the BASE resource URL, e.g.
        # https://process-mining-openai.openai.azure.com/ ; `model` = the DEPLOYMENT name.
        self.api_version = (
            config.get("embed_api_version")
            or os.getenv("AZURE_OPENAI_API_VERSION")
            or "2024-02-01"
        )
        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

        if api_key and endpoint and self.deployment:
            from urllib.parse import urlparse
            from openai import AzureOpenAI
            # AZURE_OPENAI_ENDPOINT may be the OpenAI-compatible form the chat client
            # uses (https://<res>.openai.azure.com/openai/v1/). AzureOpenAI wants the
            # BARE resource base, so normalize to scheme://host/.
            p = urlparse(endpoint)
            azure_base = f"{p.scheme}://{p.netloc}/"
            self._client = AzureOpenAI(
                api_key=api_key,
                api_version=self.api_version,
                azure_endpoint=azure_base,
            )
            self.mode = "openai"
            log.info(
                "Embedder: using Azure OpenAI embeddings (deployment=%s, api_version=%s)",
                self.deployment, self.api_version,
            )
        else:
            self._client = None
            self.mode = "fallback"
            log.warning(
                "Embedder: no embedding deployment configured; using deterministic hashed "
                "fallback (set AZURE_OPENAI_EMBED_DEPLOYMENT for real semantic embeddings)"
            )

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts -> list of vectors (same order)."""
        if self.mode == "openai":
            try:
                return self._embed_openai(texts)
            except Exception:
                log.exception("Embedder: OpenAI embedding failed; using hashed fallback for this batch")
        return [self._hash_embed(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]

    # --- backends ---
    def _embed_openai(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):           # batch to stay within request limits
            chunk = [t or "" for t in texts[i:i + 100]]
            kwargs = {"model": self.deployment, "input": chunk}
            if self.dimensions:                         # only text-embedding-3-* accept this
                kwargs["dimensions"] = int(self.dimensions)
            resp = self._client.embeddings.create(**kwargs)
            out.extend([d.embedding for d in resp.data])
        return out

    def _hash_embed(self, text: str) -> list[float]:
        """Deterministic hashed bag-of-words — each word bumps a fixed slot."""
        vec = [0.0] * self.dim
        for tok in (text or "").lower().split():
            slot = int(hashlib.md5(tok.encode("utf-8")).hexdigest(), 16) % self.dim
            vec[slot] += 1.0
        return vec
