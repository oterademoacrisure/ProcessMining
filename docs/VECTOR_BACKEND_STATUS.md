# Vector Backend — Status Note & Next Steps

*Short status on the vector-store backend and the open Azure/pgvector item.*

## Current state
- **Active backend: FAISS** — working, runs **locally with zero extra infrastructure**,
  and is our default. The backend is **swappable via one config line**
  (`vector_backend`), so changing it later is low-effort.

## Azure PostgreSQL (pgvector) — provisioned, but not usable from local
- An **Azure Database for PostgreSQL – Flexible Server** was provisioned with the
  **pgvector** extension enabled.
- **It is NOT reachable from our local machines:**
  - **Port 5432 is blocked.** Corporate **Zscaler** only allows web ports (80/443) and
    blocks outbound **TCP 5432** (the PostgreSQL port). So `psql` / the app cannot
    connect to the Azure DB directly from a laptop. (Web/HTTPS services like Azure
    OpenAI work fine because they use 443.)
  - **Managed Identity (MI) auth does not work from local.** MI provides an identity to
    **Azure-hosted** compute (VM / App Service / Container). A local dev machine is not
    an Azure resource, so it has no managed identity — MI-based authentication fails
    from local; it would only work when the app runs **inside Azure**.
- **Net:** the Azure pgvector DB can't be used for local development right now (both the
  network path and the auth model assume the app runs in Azure).

## Local pgvector — request raised
- Because the Azure DB isn't reachable locally, we **raised a request to install the
  pgvector extension on the local PostgreSQL** (local install needs admin rights + build
  tools, which are restricted on our machines, so IT is handling it).
- **Once pgvector is installed locally**, we will:
  1. Implement the **pgvector backend** (already stubbed behind the pluggable interface).
  2. **Compare it against FAISS** (search quality, performance, operational simplicity).
  3. **Decide the final go-to approach** — FAISS, pgvector, or a managed vector DB
     (e.g. Qdrant) — based on that comparison.

## Meanwhile
- **FAISS stays the working default**, so the feature is fully functional today. The
  pgvector work is additive and the switch is a one-line config change when ready.
