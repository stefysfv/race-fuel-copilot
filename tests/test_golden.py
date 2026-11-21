"""Golden snapshots of the current fueling API.

Each case posts through the FastAPI TestClient and compares the whole JSON
body with a file in tests/golden. Floats are rounded to 6 decimal places
before that comparison. A normal run never writes those files. Pass
--update-golden to rewrite them after an approved behavior change.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import app

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
CASES_PATH = GOLDEN_DIR / "cases.json"
FLOAT_DECIMALS = 6
DIFF_LIMIT = 12

_MANIFEST: dict[str, Any] = json.loads(CASES_PATH.read_text(encoding="utf-8"))
_SNAPSHOT_SKIP = frozenset(_MANIFEST["strip_keys"])
GOLDEN_CASES: list[dict[str, Any]] = list(_MANIFEST["cases"])

# Default preset, in the same order as the setup screen: full gel down to,
# full gel up to, red below, red above.
_DEFAULT_EDGES = {
    "green_min_grade": -8,
    "green_max_grade": 5,
    "red_descent_grade": -12,
    "red_climb_grade": 10,
}

# The sensitive snapshot depends on climb_sensitivity moving green max to
# +3.5%. Sending green_max 5 replaces that, so it is not a Default check.
_DEFAULT_EDGE_CASES = [
    case
    for case in GOLDEN_CASES
    if case["request"].get("athlete", {}).get("climb_sensitivity") != "sensitive"
]

client = TestClient(app)


def _strip_additive(value: Any) -> Any:
    """Drop fields that existing snapshots do not contain."""

    if isinstance(value, list):
        return [_strip_additive(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _strip_additive(item)
            for key, item in value.items()
            if key not in _SNAPSHOT_SKIP
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


def _request_for(case: dict[str, Any]) -> dict[str, Any]:
    body = copy.deepcopy(case["request"])
    fixture = case.get("gpx_fixture")
    if fixture:
        body["gpx_xml"] = (GOLDEN_DIR / fixture).read_text(encoding="utf-8")
    return body


def test_golden_manifest_is_complete() -> None:
    """Every snapshot in tests/golden must be listed in cases.json, and vice versa."""

    api_listed = {case["golden"] for case in GOLDEN_CASES}
    non_api = set(_MANIFEST.get("non_api_goldens", []))
    assert api_listed.isdisjoint(non_api)
    listed = api_listed | non_api
    on_disk = {p.name for p in GOLDEN_DIR.glob("*.json") if p.name != "cases.json"}
    assert listed == on_disk, (
        f"cases.json and tests/golden/*.json disagree. "
        f"Missing from cases.json: {sorted(on_disk - listed)}. "
        f"Missing on disk: {sorted(listed - on_disk)}."
    )


@pytest.mark.parametrize("case", GOLDEN_CASES, ids=[c["name"] for c in GOLDEN_CASES])
def test_golden_case(case: dict[str, Any], update_golden: bool) -> None:
    payload = _post(f"/api/{case['endpoint']}", _request_for(case))
    if case["name"] == "generate-plan__custom-gpx":
        names = [leg["to_name"] for leg in payload["aid_legs"]]
        assert "Bezbog" in names
    elif case["name"] == "recalibrate__mid-race":
        assert payload["snap"]["on_course"] is True
        assert payload["gi_rescue_applied"] is False
    elif case["name"] == "recalibrate__gi-rescue":
        assert payload["gi_rescue_applied"] is True
    elif case["name"] == "recalibrate__off-course":
        assert payload["snap"]["on_course"] is False
    assert_golden(case["golden"], payload, update_golden)


@pytest.mark.parametrize("case", _DEFAULT_EDGE_CASES, ids=[c["name"] for c in _DEFAULT_EDGE_CASES])
def test_explicit_default_edges_match_golden(case: dict[str, Any], update_golden: bool) -> None:
    """(-8, +5, -12, +10) matches the snapshot built with those lines omitted."""

    if update_golden:
        pytest.skip("explicit defaults are checked against the existing snapshots")
    body = _request_for(case)
    body["athlete"] = {**body.get("athlete", {}), **_DEFAULT_EDGES}
    payload = _post(f"/api/{case['endpoint']}", body)
    assert_golden(case["golden"], payload, False)
