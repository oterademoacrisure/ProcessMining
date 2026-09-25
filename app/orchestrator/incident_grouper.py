"""Groups findings into incidents based on shared correlation keys + time.

Two findings are linked into the same incident if they share at least one of:
  - a server_id, AND their time windows overlap (with a grace period)
  - a case_id, AND their time windows overlap

Findings that share nothing (or have no correlation keys) form singleton
incidents. Union-find under the hood: each finding starts as its own group;
we walk all pairs and union them when they share a link.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Sequence

from app.db.models import Finding as FindingRow


SEVERITY_RANK = {"normal": 0, "high": 1, "critical": 2}


@dataclass
class Incident:
    """A group of findings the dispatcher will hand to one investigator call."""
    findings: list[FindingRow]

    @property
    def primary_finding(self) -> FindingRow:
        """The "main" finding — highest severity, earliest time on tiebreak.
        Used as RootCauseReport.trigger_finding_id."""
        return max(
            self.findings,
            key=lambda f: (
                SEVERITY_RANK.get(f.severity, 0),
                -(f.time_max.timestamp() if f.time_max else 0),
            ),
        )

    @property
    def all_server_ids(self) -> list[str]:
        out: set[str] = set()
        for f in self.findings:
            out.update(_jsonb_list_values(f.server_ids))
        return sorted(out)

    @property
    def all_case_ids(self) -> list[int]:
        out: set[int] = set()
        for f in self.findings:
            out.update(_jsonb_list_values(f.case_ids))
        return sorted(out)

    @property
    def time_min(self) -> datetime | None:
        ts = [f.time_min for f in self.findings if f.time_min]
        return min(ts) if ts else None

    @property
    def time_max(self) -> datetime | None:
        ts = [f.time_max for f in self.findings if f.time_max]
        return max(ts) if ts else None


def group_findings_into_incidents(
    findings: Sequence[FindingRow],
    time_overlap_grace_minutes: int = 2,
) -> list[Incident]:
    """Cluster findings into incidents.

    Returns one Incident per connected component in the correlation graph.
    """
    n = len(findings)
    if n == 0:
        return []

    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    grace = timedelta(minutes=time_overlap_grace_minutes)

    for i in range(n):
        for j in range(i + 1, n):
            if _findings_linked(findings[i], findings[j], grace):
                union(i, j)

    groups: dict[int, list[FindingRow]] = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(findings[i])

    return [Incident(findings=g) for g in groups.values()]


def _findings_linked(a: FindingRow, b: FindingRow, grace: timedelta) -> bool:
    if a.tenant_id != b.tenant_id:
        return False
    if not _time_windows_overlap(a, b, grace):
        return False
    if _shared_server(a, b):
        return True
    if _shared_case(a, b):
        return True
    return False


def _time_windows_overlap(a: FindingRow, b: FindingRow, grace: timedelta) -> bool:
    a_min, a_max = a.time_min, a.time_max
    b_min, b_max = b.time_min, b.time_max
    if not (a_min and a_max and b_min and b_max):
        return False
    return (a_min - grace) <= b_max and (b_min - grace) <= a_max


def _shared_server(a: FindingRow, b: FindingRow) -> bool:
    sa, sb = set(_jsonb_list_values(a.server_ids)), set(_jsonb_list_values(b.server_ids))
    return bool(sa and sb and sa & sb)


def _shared_case(a: FindingRow, b: FindingRow) -> bool:
    ca, cb = set(_jsonb_list_values(a.case_ids)), set(_jsonb_list_values(b.case_ids))
    return bool(ca and cb and ca & cb)


def _jsonb_list_values(field) -> Iterable:
    if not field:
        return []
    if isinstance(field, dict):
        return field.get("values") or []
    if isinstance(field, list):
        return field
    return []
