"""POINT 19 (Task #19): seed a curated set of DEMO historical incidents.

SYNTHETIC demo data — for PoC/meeting demos only, NOT real ServiceNow data.
Covers the systems the standard demo incident touches (credit-bureau-prod,
docstore-prod, customer-service-prod, appian-webapp-03, loan-processing-mule-app)
with a deliberate mix so the precedent panel shows the full range:

  - exact recurrence (correlation_id matches what the investigator computes)
        -> ~0.99 confidence
  - same-system matches (cmdb_ci only)                 -> ~0.70
  - older vs newer credit-bureau fixes                 -> recency tiebreak
  - downstream-fault vs our-fault resolutions          -> nice talking point

All resolved BEFORE the demo incident (2026-05-25) so they pass the recency
guard. Idempotent (upsert by sys_id) — safe to re-run.

Usage:
    serverops\\venv\\Scripts\\python.exe serverops\\scripts\\seed_demo_history.py --tenant 1
"""
from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

SERVEROPS_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(SERVEROPS_ROOT, ".env"))
sys.path.insert(0, SERVEROPS_ROOT)

from app.db.session import SessionLocal
from app.servicenow_history.loader import load_incidents


def _rec(**kw):
    kw.setdefault("sys_updated_on", kw.get("resolved_at"))
    kw.setdefault("outcome", "approved")   # POINT 19: most demo fixes worked; override per record
    kw["_raw"] = {"_seeded": True, "number": kw.get("number")}
    return kw


# POINT 19: curated DEMO precedents (synthetic).
DEMO_INCIDENTS = [
    # 1) EXACT RECURRENCE for the credit-bureau incident (correlation_id matches
    #    rca-t{tenant}-mule-credit-bureau-prod-critical). Newest -> wins ties.
    _rec(
        number="INC0009999", sys_id="seed-creditbureau-0001",
        short_description="Credit bureau integration timing out across loan applications",
        description="Appian credit-check calls to credit-bureau-prod returned SocketTimeoutException; many loan cases stalled.",
        close_notes="Downstream cause: credit-bureau-prod API gateway was overloaded. Paged the vendor on-call; they restarted the gateway and added capacity. Timeouts cleared in minutes. No Appian/Mule change required.",
        category="Software", subcategory="Integration", cmdb_ci="credit-bureau-prod",
        priority="1 - Critical", state="Closed",
        opened_at="2026-04-15 14:20:00", resolved_at="2026-04-15 15:05:00",
        correlation_id="rca-t1-mule-credit-bureau-prod-critical",
    ),
    # 2) SAME-SYSTEM credit-bureau, different symptom (TLS), no recurrence.
    _rec(
        number="INC0011200", sys_id="seed-creditbureau-0002",
        short_description="Credit bureau calls failing with TLS handshake errors",
        description="credit-bureau-prod calls failed with SSL/TLS handshake exceptions; connection could not be established.",
        close_notes="credit-bureau-prod TLS certificate had expired. Vendor renewed it; connections restored. Added a 30-day cert-expiry monitor for downstream endpoints.",
        category="Software", subcategory="Integration", cmdb_ci="credit-bureau-prod",
        priority="2 - High", state="Closed",
        opened_at="2026-03-01 08:30:00", resolved_at="2026-03-01 09:20:00",
        correlation_id=None,
    ),
    # 3) SAME-SYSTEM credit-bureau, OLDER (demonstrates recency tiebreak vs #1/#2).
    _rec(
        number="INC0010500", sys_id="seed-creditbureau-0003",
        short_description="Slow credit bureau responses degrading credit-check flow",
        description="Credit-check latency rose above 8s; slow calls piled up and Appian processes queued.",
        close_notes="Downstream under-provisioned during a campaign spike. Vendor scaled horizontally. Recommended latency alerting at >5s.",
        category="Software", subcategory="Integration", cmdb_ci="credit-bureau-prod",
        priority="2 - High", state="Closed",
        opened_at="2025-11-10 11:00:00", resolved_at="2025-11-10 12:30:00",
        correlation_id=None, outcome="unknown",   # POINT 19: outcome not recorded -> neutral context
    ),
    # 4) docstore-prod (same-system for the docFetch part of the incident).
    _rec(
        number="INC0012050", sys_id="seed-docstore-0001",
        short_description="Document fetch integration timing out (docstore-prod)",
        description="docFetch integration to docstore-prod timed out with SocketTimeoutException; document-attach steps failed.",
        close_notes="docstore-prod ran out of disk on its primary volume. Cleared old temp files and expanded the volume; docFetch latency returned to normal. Downstream-only fix.",
        category="Software", subcategory="Integration", cmdb_ci="docstore-prod",
        priority="2 - High", state="Closed",
        opened_at="2026-04-20 09:45:00", resolved_at="2026-04-20 10:40:00",
        correlation_id="rca-t1-mule-docstore-prod-high",
    ),
    # 5) customer-service-prod (for the customer-service incident).
    _rec(
        number="INC0012099", sys_id="seed-custsvc-0001",
        short_description="Customer service API returning HTTP 500 on customer lookup",
        description="Mule customer-lookup flow received HTTP 500 from customer-service-prod; lookups failed intermittently.",
        close_notes="A bad deployment to customer-service-prod introduced a null-pointer in the lookup endpoint. Owners rolled back the deployment; 500s stopped immediately. Do not retry from Appian/Mule during a downstream bad-deploy.",
        category="Software", subcategory="Integration", cmdb_ci="customer-service-prod",
        priority="1 - Critical", state="Closed",
        opened_at="2026-05-01 16:10:00", resolved_at="2026-05-01 16:55:00",
        correlation_id="rca-t1-mule-customer-service-prod-critical",
    ),
    # 6) appian-webapp-03 — an OUR-SIDE (not downstream) fix, for contrast.
    _rec(
        number="INC0011880", sys_id="seed-appianwebapp-0001",
        short_description="Appian webapp out-of-memory causing process exceptions",
        description="appian-webapp-03 logged OutOfMemoryError and HikariPool connection timeouts; paused-by-exception processes spiked.",
        close_notes="JVM heap on appian-webapp-03 was undersized. Increased -Xmx from 2g to 4g and restarted the pod. This was an APPIAN-SIDE resource issue, not a downstream failure.",
        category="Software", subcategory="Application", cmdb_ci="appian-webapp-03",
        priority="1 - Critical", state="Closed",
        opened_at="2026-04-28 13:30:00", resolved_at="2026-04-28 14:20:00",
        correlation_id=None,
    ),
    # 7) loan-processing-mule-app — a MULE-SIDE fix, for contrast.
    _rec(
        number="INC0011750", sys_id="seed-muleapp-0001",
        short_description="Mule HTTP connector pool exhaustion",
        description="loan-processing-mule-app exhausted its HTTP connector pool; outbound integration calls queued and timed out.",
        close_notes="MULE-SIDE failure (not downstream): the HTTP requester pool was too small for peak concurrency. Increased maxConnections from 50 to 200 and redeployed the Mule app.",
        category="Software", subcategory="Integration", cmdb_ci="loan-processing-mule-app",
        priority="1 - Critical", state="Closed",
        opened_at="2026-04-22 15:00:00", resolved_at="2026-04-22 16:10:00",
        correlation_id="rca-t1-mule-mule-runtime-critical",
    ),
    # 8) REJECTED attempt on credit-bureau — a fix that was TRIED and did NOT work.
    #    Same-system as #1/#2/#3, so it surfaces for the credit-bureau incident and
    #    demonstrates the cautionary "tried & failed — avoid this" precedent label.
    _rec(
        number="INC0011010", sys_id="seed-creditbureau-0004",
        short_description="Credit bureau timeouts — attempted Appian-side connector restart",
        description="credit-bureau-prod calls timing out; on-call first suspected our side and restarted the Appian credit-check connector pool.",
        close_notes="Restarting the Appian connector pool did NOT resolve the timeouts — the fault was downstream at credit-bureau-prod. Do not restart Appian/Mule connectors for this symptom; escalate to the bureau vendor instead.",
        category="Software", subcategory="Integration", cmdb_ci="credit-bureau-prod",
        priority="1 - Critical", state="Closed",
        opened_at="2026-02-10 10:00:00", resolved_at="2026-02-10 11:30:00",
        correlation_id=None, outcome="rejected",
    ),
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed curated DEMO ServiceNow history (Task #19)")
    parser.add_argument("--tenant", type=int, default=1)
    args = parser.parse_args()

    with SessionLocal() as s:
        result = load_incidents(s, args.tenant, DEMO_INCIDENTS)
        s.commit()
    print(f"seeded demo history for tenant {args.tenant}: {result}")
    print(f"{len(DEMO_INCIDENTS)} curated incidents across "
          "credit-bureau-prod / docstore-prod / customer-service-prod / appian-webapp-03 / loan-processing-mule-app")


if __name__ == "__main__":
    main()
