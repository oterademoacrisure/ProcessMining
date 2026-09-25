"""
Pega integration-trace simulator — generates a realistic Pega integration
trace CSV by appending rows over time.

Output: a single, append-only file
`data/input/pega/pega_integration_trace.csv` with columns:

    Timestamp, Interaction ID, Service Name, Operation, Operator ID,
    Case ID, Success, Timeout, HTTP Status, Total Time (ms),
    Remote System, Error Message

Unlike the JSONL metrics simulator (which writes one file per tick), Pega
connector logs are appended to a single file. The pega_integration_trace_reader
tracks a byte offset per file, so each poll yields only the rows added since
the previous commit — mirroring how Appian's integration_trace.csv is read.

The simulator does NOT touch the database.

Usage:
    python app/pega_simulator.py --scenario=normal
    python app/pega_simulator.py --scenario=slow
    python app/pega_simulator.py --scenario=problem
    python app/pega_simulator.py --scenario=problem --interval=30 --duration=600
"""

import sys
import os
import csv
import time
import random
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.paths import INPUT_DIR


PEGA_OUTPUT_DIR = INPUT_DIR / "pega"
PEGA_LOG_FILE = PEGA_OUTPUT_DIR / "pega_integration_trace.csv"

CSV_COLUMNS = [
    "Timestamp",
    "Interaction ID",
    "Service Name",
    "Operation",
    "Operator ID",
    "Case ID",
    "Success",
    "Timeout",
    "HTTP Status",
    "Total Time (ms)",
    "Remote System",
    "Error Message",
]


# ---------------------------------------------------------------------------
# Pega connector / service definition
# ---------------------------------------------------------------------------
PEGA_SERVICES = [
    {"service": "CreditCheckService",    "operation": "POST", "remote": "ext-credit-bureau",   "base_ms": 180},
    {"service": "CustomerLookupService", "operation": "GET",  "remote": "crm-customer-svc",    "base_ms": 95},
    {"service": "DocumentService",       "operation": "GET",  "remote": "ecm-docstore",        "base_ms": 220},
    {"service": "PaymentGatewayService", "operation": "POST", "remote": "ext-payment-gateway", "base_ms": 320},
    {"service": "NotificationService",   "operation": "POST", "remote": "smtp-notify-svc",     "base_ms": 140},
]

# Number of integration calls emitted per service, per tick.
CALLS_PER_SERVICE = 3


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def _jitter_ms(base: float, pct: float = 12.0) -> int:
    delta = base * (pct / 100)
    return max(1, int(base + random.uniform(-delta, delta)))


def _new_ids() -> tuple[str, str, str]:
    interaction_id = f"INT-{random.randint(10**11, 10**12 - 1)}"
    operator_id = f"operator-{random.randint(1, 40):03d}"
    case_id = f"C-{random.randint(100000, 999999)}"
    return interaction_id, operator_id, case_id


def _build_row(svc: dict, *, success: bool, timeout: bool, http_status: int,
               total_ms: int, error_message: str = "") -> dict:
    interaction_id, operator_id, case_id = _new_ids()
    return {
        "Timestamp":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "Interaction ID":   interaction_id,
        "Service Name":     svc["service"],
        "Operation":        svc["operation"],
        "Operator ID":      operator_id,
        "Case ID":          case_id,
        "Success":          "true" if success else "false",
        "Timeout":          "true" if timeout else "false",
        "HTTP Status":      http_status,
        "Total Time (ms)":  total_ms,
        "Remote System":    svc["remote"],
        "Error Message":    error_message,
    }


def _healthy_call(svc: dict) -> dict:
    return _build_row(
        svc,
        success=True,
        timeout=False,
        http_status=200,
        total_ms=_jitter_ms(svc["base_ms"]),
    )


def _slow_call(svc: dict) -> dict:
    # Succeeds, but well above the analyzer's slow threshold (>5000ms).
    return _build_row(
        svc,
        success=True,
        timeout=False,
        http_status=200,
        total_ms=_jitter_ms(random.uniform(6000, 12000)),
    )


def _timeout_call(svc: dict) -> dict:
    total_ms = _jitter_ms(random.uniform(30000, 60000))
    return _build_row(
        svc,
        success=False,
        timeout=True,
        http_status=504,
        total_ms=total_ms,
        error_message=f"Connection to {svc['remote']} timed out after {total_ms}ms",
    )


def _failure_call(svc: dict) -> dict:
    status, message = random.choice([
        (500, f"Internal server error from {svc['remote']}"),
        (503, f"{svc['remote']} unavailable (503)"),
        (502, f"Bad gateway contacting {svc['remote']}"),
    ])
    return _build_row(
        svc,
        success=False,
        timeout=False,
        http_status=status,
        total_ms=_jitter_ms(random.uniform(400, 2500)),
        error_message=message,
    )


# ---------------------------------------------------------------------------
# Scenario engines
# ---------------------------------------------------------------------------

class NormalScenario:
    """All calls succeed quickly across every service."""

    def tick(self, tick_num: int) -> list[dict]:
        rows = []
        for svc in PEGA_SERVICES:
            for _ in range(CALLS_PER_SERVICE):
                rows.append(_healthy_call(svc))
        return rows


class SlowScenario:
    """One service degrades into a wall of slow (but successful) calls."""

    def __init__(self):
        self._slow_svc = random.choice(PEGA_SERVICES)["service"]
        print(f"  [Slow] Degraded (slow) service: {self._slow_svc}")

    def tick(self, tick_num: int) -> list[dict]:
        rows = []
        for svc in PEGA_SERVICES:
            for _ in range(CALLS_PER_SERVICE):
                if svc["service"] == self._slow_svc and random.random() < 0.8:
                    rows.append(_slow_call(svc))
                else:
                    rows.append(_healthy_call(svc))
        return rows


class ProblemScenario:
    """One service spirals into timeouts and HTTP failures."""

    def __init__(self):
        self._bad_svc = random.choice(PEGA_SERVICES)["service"]
        print(f"  [Problem] Failing service: {self._bad_svc}")

    def tick(self, tick_num: int) -> list[dict]:
        rows = []
        for svc in PEGA_SERVICES:
            for _ in range(CALLS_PER_SERVICE):
                if svc["service"] == self._bad_svc:
                    roll = random.random()
                    if roll < 0.45:
                        rows.append(_timeout_call(svc))
                    elif roll < 0.85:
                        rows.append(_failure_call(svc))
                    else:
                        rows.append(_healthy_call(svc))
                else:
                    rows.append(_healthy_call(svc))
        return rows


SCENARIOS = {
    "normal":  NormalScenario,
    "slow":    SlowScenario,
    "problem": ProblemScenario,
}


# ---------------------------------------------------------------------------
# File writer (append-only)
# ---------------------------------------------------------------------------

def _append_rows(rows: list[dict]) -> None:
    PEGA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not PEGA_LOG_FILE.exists() or PEGA_LOG_FILE.stat().st_size == 0
    with PEGA_LOG_FILE.open("a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(scenario: str, interval_sec: int = 120, duration_sec: int | None = None):
    if scenario not in SCENARIOS:
        print(f"Unknown scenario '{scenario}'. Choose from: {list(SCENARIOS)}")
        sys.exit(1)

    engine   = SCENARIOS[scenario]()
    start    = time.time()
    tick_num = 0

    print(f"\nPega simulator started — scenario={scenario}, interval={interval_sec}s"
          + (f", duration={duration_sec}s" if duration_sec else ", duration=infinite"))
    print(f"Appending to: {PEGA_LOG_FILE}")
    print("-" * 60)

    while True:
        tick_num += 1
        now = datetime.now().strftime("%H:%M:%S")
        rows = engine.tick(tick_num)
        _append_rows(rows)
        print(f"[Tick {tick_num}] {now}  appended {len(rows)} rows -> {PEGA_LOG_FILE.name}")

        if duration_sec and (time.time() - start) >= duration_sec:
            print("\nDuration reached — Pega simulator stopped.")
            break

        time.sleep(interval_sec)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pega integration-trace simulator (appends CSV)")
    parser.add_argument("--scenario", default="normal", choices=SCENARIOS.keys())
    parser.add_argument("--interval", type=int, default=120)
    parser.add_argument("--duration", type=int, default=None)
    args = parser.parse_args()

    run(scenario=args.scenario, interval_sec=args.interval, duration_sec=args.duration)
