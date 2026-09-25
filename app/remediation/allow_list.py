"""Allow-list for remediation actions.

Two layers:
  1. FORBIDDEN — destructive patterns that never run, regardless of allow-list.
                 Hard-coded so YAML overrides can't accidentally relax them.
  2. ALLOWED   — patterns the executor is permitted to run. Phase D V1 ships
                 with RESTART-ONLY. Any non-matching command is rejected with
                 a clear reason.

Action text from the LLM is expected to embed the command in backticks:
  "Restart the appian-webapp-03 pod via `kubectl rollout restart
   deployment/appian-webapp` to clear JVM heap pressure"
This module extracts the backticked command and matches against the lists.
If no backticked command is present, the action is rejected (we don't guess).
"""
from __future__ import annotations

import re
from dataclasses import dataclass


# Hard-coded forbidden patterns — match in raw command text, case-insensitive.
# These can never be allow-listed; they exist as defense-in-depth.
_FORBIDDEN_PATTERNS = [
    re.compile(r"rm\s+(-\S+\s+)*-r", re.IGNORECASE),     # rm -r, rm -rf
    re.compile(r"\bDROP\s+TABLE\b",  re.IGNORECASE),
    re.compile(r"\bDELETE\s+FROM\b", re.IGNORECASE),
    re.compile(r"\bTRUNCATE\b",      re.IGNORECASE),
    re.compile(r"mkfs",              re.IGNORECASE),
    re.compile(r"dd\s+if=",          re.IGNORECASE),
    re.compile(r":\(\)\{",                              ),  # fork bomb
    re.compile(r"shutdown",          re.IGNORECASE),
    re.compile(r"\binit\s+0\b",      re.IGNORECASE),
]


# Phase D V1 allow-list. Restart + scale only — non-destructive operations
# that are safely reversible. Each pattern must match the WHOLE command
# (re.fullmatch). Everything else is auto-rejected.
_ALLOW_PATTERNS = [
    # kubectl rollout restart deployment/<name>
    re.compile(r"kubectl\s+rollout\s+restart\s+(deployment|deploy|pod|sts|statefulset)/[\w.-]+", re.IGNORECASE),
    # kubectl scale deployment/<name> --replicas=<N>   (1-20 replicas allowed)
    re.compile(r"kubectl\s+scale\s+(deployment|deploy|sts|statefulset)/[\w.-]+\s+--replicas=([1-9]|1[0-9]|20)", re.IGNORECASE),
    # systemctl restart <service>
    re.compile(r"systemctl\s+restart\s+[\w.@-]+",                       re.IGNORECASE),
    # service <name> restart    (legacy init.d)
    re.compile(r"service\s+[\w.-]+\s+restart",                          re.IGNORECASE),
    # docker restart <container>
    re.compile(r"docker\s+restart\s+[\w.-]+",                           re.IGNORECASE),
]


_BACKTICK_RE = re.compile(r"`([^`]+)`")


@dataclass
class AllowListResult:
    allowed:  bool
    command:  str | None      # the extracted command, if any
    reason:   str             # human-readable reason (especially when denied)


def evaluate(action_text: str) -> AllowListResult:
    """Legacy path — extract backticked command from action_text, then evaluate.

    Used when an action row has no persisted `command` column (older rows
    or LLM fell back to string format).
    """
    cmd_match = _BACKTICK_RE.search(action_text or "")
    if not cmd_match:
        return AllowListResult(
            allowed=False,
            command=None,
            reason="no backticked command found in action text",
        )
    return evaluate_command(cmd_match.group(1).strip())


def evaluate_command(command: str) -> AllowListResult:
    """Primary path (Option B) — evaluate an already-extracted command string.

    The LLM puts the command directly into the `command` field of each
    structured action, so we skip the backtick-scrape and check the
    command itself against FORBIDDEN + ALLOWED patterns.
    """
    if not command or not command.strip():
        return AllowListResult(
            allowed=False,
            command=command,
            reason="empty command",
        )
    command = command.strip()

    for pat in _FORBIDDEN_PATTERNS:
        if pat.search(command):
            return AllowListResult(
                allowed=False,
                command=command,
                reason=f"forbidden pattern matched: {pat.pattern!r}",
            )

    for pat in _ALLOW_PATTERNS:
        if pat.fullmatch(command):
            return AllowListResult(
                allowed=True,
                command=command,
                reason=f"matched allow-list pattern: {pat.pattern!r}",
            )

    return AllowListResult(
        allowed=False,
        command=command,
        reason="command does not match any allow-list pattern (V1 is restart-only)",
    )