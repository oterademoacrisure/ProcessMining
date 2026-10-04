"""Central secret loading — `.env` locally, Azure Key Vault in the cloud.

Call load_secrets() once at startup (db/session.py and the MCP server already do).
Behavior:
  - Always loads `.env` first (the local-dev source / fallback).
  - If AZURE_KEY_VAULT_URL is set (i.e. running in Azure), fetches secrets from Key
    Vault via DefaultAzureCredential (Managed Identity in Azure; az login / VS Code
    sign-in locally) and puts them in os.environ — so every os.getenv(...) call just
    works, no other code changes.
  - Fully fail-safe: no Key Vault URL, missing azure libs, or any KV error -> silently
    keep the `.env` values. So local dev needs nothing from Azure.

Key Vault secret names can't contain underscores, so env var FOO_BAR maps to KV
secret FOO-BAR.
"""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Secrets the app reads via os.getenv — fetched from Key Vault when configured.
_SECRET_ENV_VARS = [
    "DATABASE_URL",
    "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT",
    "AZURE_OPENAI_EMBED_DEPLOYMENT", "AZURE_OPENAI_API_VERSION",
    "SERVICENOW_INSTANCE_URL", "SERVICENOW_USERNAME", "SERVICENOW_PASSWORD",
    "JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN",
    # POINT 21 (Task #21): Langfuse keys for LLM observability (via OTLP).
    "LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY",
    # AZURE-MONITOR: read-only service principal for Log Analytics + managed Prometheus.
    "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET",
]

_kv_loaded = False


def load_secrets() -> None:
    """Load .env, then overlay Azure Key Vault secrets if AZURE_KEY_VAULT_URL is set."""
    global _kv_loaded
    load_dotenv()                      # base / local-dev source (cheap, idempotent)

    if _kv_loaded:
        return
    kv_url = os.getenv("AZURE_KEY_VAULT_URL")
    if not kv_url:
        return                         # local: .env only — nothing from Azure
    _kv_loaded = True

    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError:
        log.warning("AZURE_KEY_VAULT_URL set but azure libs not installed; using .env only")
        return

    try:
        client = SecretClient(vault_url=kv_url, credential=DefaultAzureCredential())
        loaded = 0
        for var in _SECRET_ENV_VARS:
            try:
                value = client.get_secret(var.replace("_", "-")).value
                if value:
                    os.environ[var] = value
                    loaded += 1
            except Exception:
                pass                   # secret not in KV -> keep any .env value
        log.info("load_secrets: loaded %d secret(s) from Key Vault %s", loaded, kv_url)
    except Exception:
        log.exception("load_secrets: Key Vault access failed; falling back to .env")
