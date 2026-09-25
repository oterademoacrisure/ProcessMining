"""Action executor — Mock for Phase D V1, Real placeholder for V2.

Mode is selected via config in modules.yaml:
  executor_mode: mock     # logs the command, simulates success, no side effects
  executor_mode: dry_run  # like real but prepends `echo` / `--dry-run=client`
  executor_mode: real     # actually runs the command via subprocess

For the demo (no real cluster reachable), Mock is the only mode that works
without external credentials. The real executor is sketched but disabled
behind an explicit safety flag.
"""
from __future__ import annotations

import logging
import random
import shlex
import subprocess
import time
from dataclasses import dataclass


log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    exit_code:    int
    stdout:       str
    stderr:       str
    duration_sec: float
    mode:         str    # "mock" | "dry_run" | "real"


class BaseExecutor:
    def execute(self, command: str) -> ExecutionResult:
        raise NotImplementedError


class MockExecutor(BaseExecutor):
    """Simulates command execution. Always succeeds unless command is empty.

    Sleeps briefly to make the state transition observable in the UI.
    """
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.simulated_duration_sec = float(self.config.get("mock_duration_sec", 1.5))
        self.simulated_failure_rate = float(self.config.get("mock_failure_rate", 0.0))

    def execute(self, command: str) -> ExecutionResult:
        log.info("MockExecutor: would run -> %s", command)
        time.sleep(self.simulated_duration_sec)
        # Optional configurable random failure
        if self.simulated_failure_rate > 0 and random.random() < self.simulated_failure_rate:
            return ExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"[MOCK] simulated failure (failure_rate={self.simulated_failure_rate})",
                duration_sec=self.simulated_duration_sec,
                mode="mock",
            )
        return ExecutionResult(
            exit_code=0,
            stdout=f"[MOCK] would have run: {command}\n[MOCK] simulated exit_code=0",
            stderr="",
            duration_sec=self.simulated_duration_sec,
            mode="mock",
        )


class DryRunExecutor(BaseExecutor):
    """Like Real but prepends `echo` to render commands harmless.

    Useful for testing against a real shell context (PATH resolution,
    environment, etc.) without side effects.
    """
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.timeout_sec = int(self.config.get("real_timeout_sec", 60))

    def execute(self, command: str) -> ExecutionResult:
        wrapped = f"echo [DRY-RUN] {command}"
        return _run_subprocess(wrapped, self.timeout_sec, mode="dry_run")


class RealExecutor(BaseExecutor):
    """Actually runs the command. Requires explicit `confirm_real=true` config
    flag to guard against accidental enablement."""
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        if not self.config.get("confirm_real"):
            raise RuntimeError(
                "RealExecutor requires explicit `confirm_real: true` in the "
                "remediation config — refusing to enable without confirmation"
            )
        self.timeout_sec = int(self.config.get("real_timeout_sec", 60))

    def execute(self, command: str) -> ExecutionResult:
        return _run_subprocess(command, self.timeout_sec, mode="real")


def _run_subprocess(command: str, timeout_sec: int, mode: str) -> ExecutionResult:
    start = time.time()
    try:
        result = subprocess.run(
            shlex.split(command, posix=True),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        return ExecutionResult(
            exit_code=result.returncode,
            stdout=result.stdout[:8000],
            stderr=result.stderr[:8000],
            duration_sec=time.time() - start,
            mode=mode,
        )
    except subprocess.TimeoutExpired as e:
        return ExecutionResult(
            exit_code=124,
            stdout=(e.stdout or "")[:8000] if isinstance(e.stdout, str) else "",
            stderr=f"timeout after {timeout_sec}s",
            duration_sec=time.time() - start,
            mode=mode,
        )
    except Exception as e:
        return ExecutionResult(
            exit_code=126,
            stdout="",
            stderr=f"executor exception: {type(e).__name__}: {e}",
            duration_sec=time.time() - start,
            mode=mode,
        )


def build_executor(config: dict | None = None) -> BaseExecutor:
    cfg = config or {}
    mode = (cfg.get("executor_mode") or "mock").lower()
    if mode == "mock":
        return MockExecutor(cfg)
    if mode == "dry_run":
        return DryRunExecutor(cfg)
    if mode == "real":
        return RealExecutor(cfg)
    log.warning("Unknown executor_mode %r — falling back to mock", mode)
    return MockExecutor(cfg)