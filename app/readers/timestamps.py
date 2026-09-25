from __future__ import annotations

from datetime import datetime, timezone


def parse_iso_timestamp(value) -> datetime:
    """Parse an ISO-8601 timestamp into a timezone-aware datetime.

    Accepts strings (with optional trailing 'Z'), datetime objects (returned
    as-is, with UTC tz attached if naive), or numeric epoch seconds.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    if not isinstance(value, str):
        raise ValueError(f"Cannot parse timestamp from {type(value).__name__}: {value!r}")

    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
