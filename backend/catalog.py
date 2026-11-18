"""Read the stored race catalog. This is the file adapter; the engine never opens the file itself."""

from __future__ import annotations

import json
from pathlib import Path

from backend.models import Race


def load_race_catalog(path: Path | None = None) -> list[Race]:
    """Read `backend/data/races.json`."""

    catalog_path = path or Path(__file__).resolve().parent / "data" / "races.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    return [Race.model_validate(item) for item in payload["races"]]


def get_race(race_id: str, path: Path | None = None) -> Race:
    """Return one stored race, or raise KeyError."""

    for race in load_race_catalog(path):
        if race.id == race_id:
            return race
    raise KeyError(race_id)
