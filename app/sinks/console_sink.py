from __future__ import annotations

import json
from typing import Any, Sequence

from app.db.models import EventLog
from app.findings import Finding
from app.sinks.base import BaseSink


class ConsoleSink(BaseSink):
    """Prints analyzer findings to stdout — useful for local dev / smoke tests."""

    def handle(
        self,
        source_type: str,
        rows: Sequence[EventLog],
        result: Any,
        analyzer_class: str | None = None,
    ) -> None:
        findings: list[Finding] = result if isinstance(result, list) else []
        header = f"[{source_type}] analyzed {len(rows)} row(s) -> {len(findings)} finding(s)"
        print(header)
        for f in findings:
            print(f"  - subject={f.subject_key}  severity={f.severity}  rows={len(f.contributing_rows)}")
            if f.observation:
                print(f"    observation: {f.observation}")
            try:
                payload_str = json.dumps(f.payload, indent=2, default=str)
                indented = "\n".join("    " + line for line in payload_str.splitlines())
                print(indented)
            except (TypeError, ValueError):
                print(f"    payload: {f.payload!r}")
