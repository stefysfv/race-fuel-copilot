"""Read the stored race catalog from disk.

The catalog lives in `frontend/data` so the static site and this dev server
read the same file.
"""

from __future__ import annotations

import json
from pathlib import Path

from backend.models import Race

_DATA_DIR = Path(__file__).resolve().parents[2] / "frontend" / "data"


def load_race_catalog_dict(path: Path | None = None) -> dict:
    """Return the raw catalog object. The engine accepts this dict as-is."""

    catalog_path = path or _DATA_DIR / "races.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Race catalog must be a JSON object")
    return payload


def load_race_catalog(path: Path | None = None) -> list[Race]:
    """Read `frontend/data/races.json`."""

    payload = load_race_catalog_dict(path)
    return [Race.model_validate(item) for item in payload["races"]]


def get_race(race_id: str, path: Path | None = None) -> Race:
    """Return one stored race, or raise KeyError."""

    for race in load_race_catalog(path):
        if race.id == race_id:
            return race
    raise KeyError(race_id)
