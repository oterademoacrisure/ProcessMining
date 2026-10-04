"""AZURE-MONITOR: bearer tokens for the Azure Monitor readers.

DefaultAzureCredential picks up AZURE_TENANT_ID / AZURE_CLIENT_ID /
AZURE_CLIENT_SECRET from the environment (loaded from .env or Key Vault by
app/secrets.py), or a managed identity when running in Azure. The credential
is module-level because readers are rebuilt on every poll and the credential
caches tokens until they expire.
"""
from __future__ import annotations

import os

from app.secrets import load_secrets

LOG_ANALYTICS_SCOPE = "https://api.loganalytics.io/.default"
PROMETHEUS_SCOPE = "https://prometheus.monitor.azure.com/.default"

_credential = None


def azure_token(scope: str) -> str:
    global _credential
    load_secrets()
    # Testing only: a short-lived token from `az account get-access-token`
    # (Cloud Shell) for one resource; it expires after ~1 hour.
    manual = os.getenv("AZURE_ACCESS_TOKEN")
    if manual:
        return manual
    if _credential is None:
        from azure.identity import DefaultAzureCredential
        _credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
    return _credential.get_token(scope).token
