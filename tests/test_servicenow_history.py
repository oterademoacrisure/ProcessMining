"""POINT 19 (Task #19): unit tests for the ServiceNow-history pure logic.

These cover the deterministic helpers — no live ServiceNow and no database
needed, so they run anywhere/fast. (Retrieval scoring + loader upsert involve
Postgres full-text/SQL and are exercised by the live verification runs.)
"""
from datetime import datetime

from app.servicenow_history.client import ServiceNowHistoryClient
from app.servicenow_history.loader import _parse_dt, _trunc
from app.servicenow_history.retrieval import _or_tsquery_input
from app.investigators.llm_rca_investigator import _precedent_correlation_id


# ── retrieval: text -> OR tsquery input ───────────────────────────────────────

def test_or_tsquery_input_none_and_empty():
    assert _or_tsquery_input(None) is None
    assert _or_tsquery_input("") is None
    assert _or_tsquery_input("a to of") is None   # all tokens <= 2 chars -> nothing


def test_or_tsquery_input_tokenizes_and_filters():
    q = _or_tsquery_input("Credit-bureau timed OUT to a database")
    parts = q.split(" | ")
    assert "credit" in parts and "bureau" in parts and "out" in parts and "database" in parts
    # tokens of length <= 2 are dropped ("to", "a"); real stopword removal is
    # left to Postgres' english config at to_tsquery time, by design.
    assert "to" not in parts and "a" not in parts


def test_or_tsquery_input_dedups_and_caps():
    q = _or_tsquery_input("alpha alpha beta " + " ".join(f"word{i}" for i in range(40)))
    parts = q.split(" | ")
    assert parts.count("alpha") == 1     # dedup
    assert len(parts) <= 25              # cap


# ── client: normalize ServiceNow's display_value='all' shape ──────────────────

def test_normalize_resolves_reference_and_keeps_raw_dates():
    raw = {
        "number":            {"value": "INC1", "display_value": "INC1"},
        "sys_id":            {"value": "abc",  "display_value": "abc"},
        "short_description": {"value": "Credit bureau timeout", "display_value": "Credit bureau timeout"},
        "cmdb_ci":           {"value": "id-123", "display_value": "credit-bureau-prod"},
        "state":             {"value": "7", "display_value": "Closed"},
        "priority":          {"value": "1", "display_value": "1 - Critical"},
        "resolved_at":       {"value": "2026-05-02 15:05:00", "display_value": "05/02/2026"},
        "sys_updated_on":    {"value": "2026-05-02 15:05:00", "display_value": "x"},
    }
    n = ServiceNowHistoryClient._normalize(raw)
    assert n["number"] == "INC1"
    assert n["cmdb_ci"] == "credit-bureau-prod"      # display NAME, not the id
    assert n["state"] == "Closed"                    # display label
    assert n["priority"] == "1 - Critical"
    assert n["resolved_at"] == "2026-05-02 15:05:00" # RAW value (locale-stable)
    assert n["_raw"] is raw                          # full payload preserved


# ── loader: truncation + tolerant date parsing ────────────────────────────────

def test_trunc_caps_and_handles_empty():
    assert _trunc("priority", None) is None
    assert _trunc("priority", "") is None
    assert _trunc("priority", "1 - Critical") == "1 - Critical"
    assert len(_trunc("priority", "x" * 50)) == 16   # varchar(16) cap


def test_parse_dt():
    dt = _parse_dt("2026-05-02 15:05:00")
    assert isinstance(dt, datetime) and dt.year == 2026 and dt.hour == 15
    assert _parse_dt(None) is None
    assert _parse_dt("not-a-date") is None


# ── investigator: correlation_id mirrors the ServiceNow sink ──────────────────

class _FakeFinding:
    tenant_id = 1
    source_type = "mule"
    subject_key = "credit-bureau-prod"
    severity = "critical"


def test_precedent_correlation_id_matches_sink_recipe():
    assert _precedent_correlation_id(_FakeFinding()) == "rca-t1-mule-credit-bureau-prod-critical"


def test_precedent_correlation_id_slugs_messy_values():
    class F:
        tenant_id = 2
        source_type = "appian_integration_trace"
        subject_key = "_a-int-creditCheck-0001"
        severity = "high"
    cid = _precedent_correlation_id(F())
    assert cid.startswith("rca-t2-appian-integration-trace-")
    assert " " not in cid and cid == cid.lower()
