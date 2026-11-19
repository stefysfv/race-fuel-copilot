"""Golden snapshots of the current fueling API.

Each case posts through the FastAPI TestClient and compares the whole JSON
body with a file in tests/golden. Floats are rounded to 6 decimal places
before that comparison. A normal run never writes those files. Pass
--update-golden to rewrite them after an approved behavior change.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
FLOAT_DECIMALS = 6
DIFF_LIMIT = 12

# One third of the recorded 27 km track. 110.946 min is 1.2x the default
# plan's predicted time there, so the fix is mid-race and behind the plan.
# The off-course fix is the same clock, 250 m north of that point.
MID_RACE_LAT = 42.5632102570075
MID_RACE_LON = 23.2704473114883
MID_RACE_ELAPSED_MIN = 110.946
OFF_COURSE_LAT = 42.5654585610223

RACE_IDS = (
    "salomon-vitosha-12",
    "salomon-vitosha-27",
    "salomon-vitosha-40",
    "vitosha-100",
)

# target_time_minutes is set above the 120 min short-race line.
ATHLETES: dict[str, dict[str, Any]] = {
    "default": {},
    "hot-85kg": {"weight_kg": 85, "temperature_c": 32},
    "sensitive-early-savoury": {"climb_sensitivity": "sensitive", "early_savoury": True},
    "target-time": {"target_time_minutes": 180},
    "real-food-heavy": {"fuel_preference": "real_food_heavy"},
}

client = TestClient(app)


# Reasons, forfeited rows, and the fuel budget are additive. Older snapshots
# do not contain them, and the 70–85% info note must not land in `warnings`.
_ADDITIVE_KEYS = frozenset({"reasons", "forfeited", "fuel_budget"})


def _strip_additive(value: Any) -> Any:
    """Drop the reason fields so an existing snapshot still matches."""

    if isinstance(value, list):
        return [_strip_additive(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_additive(item)
            for key, item in value.items()
            if key not in _ADDITIVE_KEYS
        }
    return value


def _round_numbers(value: Any) -> Any:
    """Round every float to 6 decimal places. Leave ints, bools, and strings."""

    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, float):
        return float(f"{value:.{FLOAT_DECIMALS}f}")
    if isinstance(value, int):
        return value
    if isinstance(value, list):
        return [_round_numbers(item) for item in value]
    if isinstance(value, dict):
        return {key: _round_numbers(item) for key, item in value.items()}
    raise TypeError(f"Unexpected JSON value: {type(value).__name__}")


def _preview(value: Any) -> str:
    """Shorten one differing value so a failure stays readable."""

    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False)
    if len(text) > 160:
        return text[:157] + "..."
    return text


def _diff_paths(expected: Any, actual: Any, path: str = "$") -> list[tuple[str, Any, Any]]:
    """Walk two JSON values and list paths whose leaves differ."""

    if isinstance(expected, dict) and isinstance(actual, dict):
        found: list[tuple[str, Any, Any]] = []
        for key in list(expected) + [key for key in actual if key not in expected]:
            child = f"{path}.{key}"
            if key not in expected:
                found.append((child, "<missing>", actual[key]))
            elif key not in actual:
                found.append((child, expected[key], "<missing>"))
            else:
                found.extend(_diff_paths(expected[key], actual[key], child))
        return found
    if isinstance(expected, list) and isinstance(actual, list):
        found = []
        if len(expected) != len(actual):
            found.append((f"{path}.length", len(expected), len(actual)))
        for index, (left, right) in enumerate(zip(expected, actual)):
            found.extend(_diff_paths(left, right, f"{path}[{index}]"))
        return found
    if expected != actual:
        return [(path, expected, actual)]
    return []


def _format_diff(name: str, diffs: list[tuple[str, Any, Any]]) -> str:
    """Render the first differing paths and how many more were found."""

    shown = diffs[:DIFF_LIMIT]
    lines = [f"Golden snapshot differs: {name}", f"{len(diffs)} path(s) differ."]
    for path, expected, actual in shown:
        lines.append(f"  {path}")
        lines.append(f"    snapshot: {_preview(expected)}")
        lines.append(f"    actual:   {_preview(actual)}")
    remaining = len(diffs) - len(shown)
    if remaining > 0:
        lines.append(f"  ... {remaining} more path(s)")
    return "\n".join(lines)


def assert_golden(name: str, payload: Any, update: bool) -> None:
    """Compare one response with its snapshot, or rewrite that snapshot."""

    actual = _round_numbers(_strip_additive(payload))
    path = GOLDEN_DIR / name
    if update:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    if not path.is_file():
        raise AssertionError(
            f"Missing golden snapshot {path}. Re-run with --update-golden to write it."
        )
    expected = _strip_additive(json.loads(path.read_text(encoding="utf-8")))
    diffs = _diff_paths(expected, actual)
    if diffs:
        raise AssertionError(_format_diff(name, diffs))


def _post(path: str, body: dict[str, Any]) -> Any:
    """Post one API call and return the JSON body of a 200 response."""

    response = client.post(path, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _course_gpx() -> str:
    """A short uploaded track: 6 km heading north at a steady +2% grade."""

    lat = 42.58
    lon = 23.29
    elevation = 1500.0
    meters_per_deg = 6_371_000.0 * 3.141592653589793 / 180.0
    step_m = 50.0
    rise = (2.0 / 100.0) * step_m
    dlat = step_m / meters_per_deg
    rows = [f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{elevation:.1f}</ele></trkpt>']
    for _ in range(120):
        lat += dlat
        elevation += rise
        rows.append(f'<trkpt lat="{lat:.6f}" lon="{lon:.6f}"><ele>{elevation:.1f}</ele></trkpt>')
    body = "".join(rows)
    return f'<?xml version="1.0"?><gpx version="1.1"><trk><trkseg>{body}</trkseg></trk></gpx>'


@pytest.mark.parametrize("race_id", RACE_IDS)
@pytest.mark.parametrize("athlete_name", list(ATHLETES))
def test_catalog_plan_golden(race_id: str, athlete_name: str, update_golden: bool) -> None:
    payload = _post(
        "/api/generate-plan",
        {"race_id": race_id, "athlete": ATHLETES[athlete_name]},
    )
    assert_golden(f"generate-plan__{race_id}__{athlete_name}.json", payload, update_golden)


def test_custom_gpx_golden(update_golden: bool) -> None:
    payload = _post(
        "/api/generate-plan",
        {
            "gpx_xml": _course_gpx(),
            "athlete": {},
            "aid_stations": [
                {
                    "name": "Bezbog",
                    "distance_km": 2.03,
                    "has_water": True,
                    "has_food": True,
                    "cutoff_hours": 6.5,
                }
            ],
        },
    )
    names = [leg["to_name"] for leg in payload["aid_legs"]]
    assert "Bezbog" in names
    assert_golden("generate-plan__custom-gpx.json", payload, update_golden)


def _recalibrate(extra: dict[str, Any]) -> Any:
    """Recalibrate the 27 km catalog race from a shared mid-race clock."""

    body = {
        "race_id": "salomon-vitosha-27",
        "athlete": {},
        "latitude": MID_RACE_LAT,
        "longitude": MID_RACE_LON,
        "elapsed_min": MID_RACE_ELAPSED_MIN,
        "gi_rescue": False,
    }
    body.update(extra)
    return _post("/api/recalibrate", body)


def test_recalibrate_mid_race_golden(update_golden: bool) -> None:
    payload = _recalibrate({})
    assert payload["snap"]["on_course"] is True
    assert payload["gi_rescue_applied"] is False
    assert_golden("recalibrate__mid-race.json", payload, update_golden)


def test_recalibrate_gi_rescue_golden(update_golden: bool) -> None:
    payload = _recalibrate({"gi_rescue": True})
    assert payload["gi_rescue_applied"] is True
    assert_golden("recalibrate__gi-rescue.json", payload, update_golden)


def test_recalibrate_off_course_golden(update_golden: bool) -> None:
    payload = _recalibrate({"latitude": OFF_COURSE_LAT})
    assert payload["snap"]["on_course"] is False
    assert_golden("recalibrate__off-course.json", payload, update_golden)
