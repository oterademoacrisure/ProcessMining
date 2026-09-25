"""
Data simulator — generates realistic metrics and writes JSONL files.

Output: one file per tick under data/input/simulator/, named
`tick-<YYYYMMDDHHMMSS>-<scenario>.jsonl`. Each line is one app sample.

The simulator does NOT touch the database. The simulator_file_reader
consumes these files and writes to EVENT_LOG.

Usage:
    python app/data_simulator.py --scenario=normal
    python app/data_simulator.py --scenario=spike
    python app/data_simulator.py --scenario=problem
    python app/data_simulator.py --scenario=spike   --interval=30
    python app/data_simulator.py --scenario=problem --interval=30 --duration=600
"""

import sys
import os
import time
import json
import random
import argparse
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from app.paths import INPUT_DIR


SIMULATOR_OUTPUT_DIR = INPUT_DIR / "simulator"


# ---------------------------------------------------------------------------
# Application fleet definition
# ---------------------------------------------------------------------------
APPS = [
    {"system_id": "ubuntu-prod-01",    "application_name": "NodeExporter",    "hostname": "ubuntu-prod-01",    "tier": "1", "cpu_count": 4,  "mem_gb": 16.0,   "storage_gb": 512.0},
    {"system_id": "api-gateway-prod",  "application_name": "API_Gateway",     "hostname": "api-gateway-prod",  "tier": "1", "cpu_count": 8,  "mem_gb": 32.0,   "storage_gb": 1024.0},
    {"system_id": "postgres-db-prod",  "application_name": "PostgreSQL_DB",   "hostname": "postgres-db-prod",  "tier": "2", "cpu_count": 16, "mem_gb": 64.0,   "storage_gb": 2048.0},
    {"system_id": "ml-pipeline-prod",  "application_name": "ML_Pipeline",     "hostname": "ml-pipeline-prod",  "tier": "2", "cpu_count": 32, "mem_gb": 128.0,  "storage_gb": 4096.0},
    {"system_id": "kafka-broker-prod", "application_name": "Kafka_Broker",    "hostname": "kafka-broker-prod", "tier": "2", "cpu_count": 8,  "mem_gb": 32.0,   "storage_gb": 2048.0},
    {"system_id": "nginx-web-prod",    "application_name": "Nginx_WebServer", "hostname": "nginx-web-prod",    "tier": "3", "cpu_count": 4,  "mem_gb": 8.0,    "storage_gb": 256.0},
]


# ---------------------------------------------------------------------------
# Metric generators per scenario
# ---------------------------------------------------------------------------

def _jitter(base: float, pct: float = 5.0) -> int:
    delta = base * (pct / 100)
    return max(0, min(100, int(base + random.uniform(-delta, delta))))


def _wait_type(cpu: int, iowait_hint: float) -> str:
    if iowait_hint > 20:
        return "IO"
    if cpu > 50:
        return "CPU"
    return "None"


def generate_normal(app: dict) -> dict:
    cpu    = _jitter(random.uniform(15, 40))
    mem    = _jitter(random.uniform(30, 55))
    iowait = random.uniform(1, 8)
    conns  = int(random.uniform(10, 80))
    return _build_row(app, cpu, mem, iowait, conns, slow_query=0)


def generate_spike(app: dict, is_spiking: bool) -> dict:
    if is_spiking:
        cpu    = _jitter(random.uniform(88, 99), pct=2)
        mem    = _jitter(random.uniform(75, 92), pct=2)
        iowait = random.uniform(5, 15)
        conns  = int(random.uniform(400, 900))
    else:
        cpu    = _jitter(random.uniform(15, 40))
        mem    = _jitter(random.uniform(30, 55))
        iowait = random.uniform(1, 8)
        conns  = int(random.uniform(10, 80))
    return _build_row(app, cpu, mem, iowait, conns, slow_query=int(is_spiking) * random.randint(5, 20))


def generate_problem(app: dict, tick: int, is_degraded: bool) -> dict:
    if is_degraded:
        ramp    = min(1.0, tick / 5)
        cpu     = _jitter(80 + ramp * 18, pct=2)
        mem     = _jitter(80 + ramp * 15, pct=2)
        iowait  = random.uniform(8, 28)
        conns   = int(300 + ramp * 700)
        slow_q  = int(ramp * 50)
    else:
        cpu     = _jitter(random.uniform(15, 35))
        mem     = _jitter(random.uniform(30, 50))
        iowait  = random.uniform(1, 6)
        conns   = int(random.uniform(10, 60))
        slow_q  = 0
    return _build_row(app, cpu, mem, iowait, conns, slow_query=slow_q)


def _build_row(app: dict, cpu: int, mem: int, iowait: float, conns: int, slow_query: int) -> dict:
    storage_pct = _jitter(random.uniform(30, 70))
    return {
        "timestamp":            datetime.now(timezone.utc).isoformat(),
        "system_id":            app["system_id"],
        "application_name":     app["application_name"],
        "hostname":             app["hostname"],
        "tier":                 app["tier"],
        "cpu_allocated_vcpu":   app["cpu_count"],
        "cpu_usage_pct":        cpu,
        "memory_allocated_gb":  app["mem_gb"],
        "memory_usage_pct":     mem,
        "storage_allocated_gb": app["storage_gb"],
        "storage_usage_pct":    storage_pct,
        "active_connections":   conns,
        "slow_query_count":     slow_query,
        "wait_type":            _wait_type(cpu, iowait),
    }


# ---------------------------------------------------------------------------
# Scenario engines
# ---------------------------------------------------------------------------

class NormalScenario:
    def tick(self, tick_num: int):
        return [generate_normal(app) for app in APPS]


class SpikeScenario:
    def __init__(self):
        self._spike_app   = None
        self._ticks_left  = 0

    def tick(self, tick_num: int):
        if self._ticks_left == 0:
            if random.random() < 0.3:
                self._spike_app  = random.choice(APPS)["system_id"]
                self._ticks_left = random.randint(2, 4)
                print(f"  [Spike] {self._spike_app} spiking for {self._ticks_left} tick(s)")
        else:
            self._ticks_left -= 1

        rows = []
        for app in APPS:
            is_spiking = (app["system_id"] == self._spike_app and self._ticks_left >= 0)
            rows.append(generate_spike(app, is_spiking))
        if self._ticks_left == 0:
            self._spike_app = None
        return rows


class ProblemScenario:
    def __init__(self):
        self._degraded_app = random.choice(APPS)["system_id"]
        print(f"  [Problem] Degraded app: {self._degraded_app}")

    def tick(self, tick_num: int):
        rows = []
        for app in APPS:
            is_degraded = (app["system_id"] == self._degraded_app)
            rows.append(generate_problem(app, tick_num, is_degraded))
        return rows


SCENARIOS = {
    "normal":  NormalScenario,
    "spike":   SpikeScenario,
    "problem": ProblemScenario,
}


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def _write_tick(rows: list[dict], scenario: str) -> Path:
    SIMULATOR_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
    out_path = SIMULATOR_OUTPUT_DIR / f"tick-{stamp}-{scenario}.jsonl"
    tmp_path = out_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    tmp_path.replace(out_path)
    return out_path


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run(scenario: str, interval_sec: int = 120, duration_sec: int | None = None):
    if scenario not in SCENARIOS:
        print(f"Unknown scenario '{scenario}'. Choose from: {list(SCENARIOS)}")
        sys.exit(1)

    engine    = SCENARIOS[scenario]()
    start     = time.time()
    tick_num  = 0

    print(f"\nSimulator started — scenario={scenario}, interval={interval_sec}s"
          + (f", duration={duration_sec}s" if duration_sec else ", duration=infinite"))
    print(f"Writing to: {SIMULATOR_OUTPUT_DIR}")
    print("-" * 60)

    while True:
        tick_num += 1
        now = datetime.now().strftime("%H:%M:%S")
        rows = engine.tick(tick_num)
        path = _write_tick(rows, scenario)
        print(f"[Tick {tick_num}] {now}  wrote {len(rows)} rows -> {path.name}")

        if duration_sec and (time.time() - start) >= duration_sec:
            print("\nDuration reached — simulator stopped.")
            break

        time.sleep(interval_sec)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Metrics simulator (writes JSONL)")
    parser.add_argument("--scenario",  default="normal",   choices=SCENARIOS.keys())
    parser.add_argument("--interval",  type=int, default=120)
    parser.add_argument("--duration",  type=int, default=None)
    args = parser.parse_args()

    run(scenario=args.scenario, interval_sec=args.interval, duration_sec=args.duration)
