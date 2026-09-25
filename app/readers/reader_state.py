from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.paths import STATE_DIR


def state_path(source_type: str) -> Path:
    return STATE_DIR / f"{source_type}.json"


def load_state(source_type: str) -> dict[str, Any]:
    path = state_path(source_type)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f) or {}


def save_state(source_type: str, state: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    path = state_path(source_type)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    tmp.replace(path)
