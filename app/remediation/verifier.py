"""Post-execution verifier.

After the executor runs an action, the verifier checks whether the symptom
the LLM identified has actually recovered. Phase D V1 ships a
confidence-driven mock:
  - report confidence >= threshold (default 0.7) -> verified
  - report confidence <  threshold              -> unverified

This ties verification to the same coverage/confidence framework as the
rest of the system — actions taken on high-confidence RCAs are "trusted"
to recover; low-confidence ones are flagged for human review.

A real verifier (V2) would re-poll the actual metric the LLM identified
(e.g. CPU on the target host) and compare against a pre-execution baseline.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass


log = logging.getLogger(__name__)


@dataclass
class VerifyResult:
    passed:  bool
    details: str
    duration_sec: float


class BaseVerifier:
    def verify(self, *, action_text: str, command: str, report_confidence: float | None) -> VerifyResult:
        raise NotImplementedError


class ConfidenceDrivenMockVerifier(BaseVerifier):
    """Mock that bases its outcome on the RCA report's LLM confidence.

    Phase D V1 default. Makes the demo legible: if the LLM was confident
    in the hypothesis, the remediation "works"; if not, it doesn't.
    """
    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self.confidence_threshold = float(self.config.get("verify_confidence_threshold", 0.7))
        self.simulated_wait_sec    = float(self.config.get("mock_verify_wait_sec", 1.0))

    def verify(self, *, action_text: str, command: str, report_confidence: float | None) -> VerifyResult:
        time.sleep(self.simulated_wait_sec)
        # Treat missing confidence as "neither pass nor fail" — bias toward pass
        # so that pipelines without LLM confidence info don't get stuck.
        conf = report_confidence if report_confidence is not None else 0.75
        if conf >= self.confidence_threshold:
            return VerifyResult(
                passed=True,
                details=(
                    f"[MOCK] Simulated metric returned to baseline within window "
                    f"(report confidence {conf:.2f} >= {self.confidence_threshold:.2f})"
                ),
                duration_sec=self.simulated_wait_sec,
            )
        return VerifyResult(
            passed=False,
            details=(
                f"[MOCK] Simulated metric still elevated after window "
                f"(report confidence {conf:.2f} < {self.confidence_threshold:.2f}; "
                f"low-confidence hypothesis did not converge)"
            ),
            duration_sec=self.simulated_wait_sec,
        )


class AlwaysPassVerifier(BaseVerifier):
    """Useful for demos where you want every action to succeed regardless."""
    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def verify(self, *, action_text: str, command: str, report_confidence: float | None) -> VerifyResult:
        return VerifyResult(passed=True, details="[MOCK] always-pass verifier", duration_sec=0.0)


def build_verifier(config: dict | None = None) -> BaseVerifier:
    cfg = config or {}
    mode = (cfg.get("verifier_mode") or "confidence_driven").lower()
    if mode == "confidence_driven":
        return ConfidenceDrivenMockVerifier(cfg)
    if mode == "always_pass":
        return AlwaysPassVerifier(cfg)
    log.warning("Unknown verifier_mode %r — falling back to confidence_driven", mode)
    return ConfidenceDrivenMockVerifier(cfg)