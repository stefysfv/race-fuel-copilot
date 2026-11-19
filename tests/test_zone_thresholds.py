"""Athlete zone lines. Omitted edges keep today's constants.

Stricter means a lower red-climb line, a red-descent line closer to zero,
a lower full-gel climb line, and a full-gel descent line closer to zero.
Red share only moves when a red line moves. It never goes down as the
lines get stricter.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st
from pydantic import ValidationError

from backend.engine.constants import (
    GREEN_MAX_GRADE,
    GREEN_MIN_GRADE,
    YELLOW_DOWNHILL_MIN,
    YELLOW_UPHILL_MAX,
)
from backend.engine.entry import preview_zones_json
from backend.engine.zones import ZoneEdges, classify_zone, preview_course_zones, zone_edges_for
from backend.gpx_parser import prepare_course, slice_segments
from backend.main import app
from backend.models import AthleteInput, DigestiveZone
from tests.test_fuel_engine import build_plan, course_points, to_gpx

# Setup-screen presets. Order is full-gel floor, full-gel ceiling, red below, red above.
PRESETS: dict[str, tuple[float, float, float, float]] = {
    "sensitive": (-6.0, 3.5, -10.0, 8.0),
    "default": (-8.0, 5.0, -12.0, 10.0),
    "iron": (-10.0, 8.0, -16.0, 15.0),
}

# Half-percent steps stay exact in binary floating point, same as the sliders.
_MIXED = course_points(
    [
        (800, -15.0),
        (800, -10.0),
        (800, -4.0),
        (2_000, 0.0),
        (800, 4.0),
        (800, 8.0),
        (800, 12.0),
        (800, 18.0),
    ]
)
_SEGMENTS = slice_segments(prepare_course(_MIXED))
client = TestClient(app)


def _edges(green_min: float, green_max: float, red_descent: float, red_climb: float) -> ZoneEdges:
    return ZoneEdges(
        green_max=green_max,
        green_min=green_min,
        red_climb=red_climb,
        red_descent=red_descent,
    )


def _athlete(edges: ZoneEdges) -> AthleteInput:
    return AthleteInput(
        green_min_grade=edges.green_min,
        green_max_grade=edges.green_max,
        red_descent_grade=edges.red_descent,
        red_climb_grade=edges.red_climb,
    )


def _half(ticks: int) -> float:
    return ticks / 2


def _is_red(grade: float, edges: ZoneEdges) -> bool:
    return classify_zone(grade, *edges) is DigestiveZone.red


def _probe_grades(loose: ZoneEdges, strict: ZoneEdges) -> list[float]:
    """Half-percent grades across the slider range, plus each drawn boundary."""

    grades = [_half(ticks) for ticks in range(-60, 61)]
    for edges in (loose, strict):
        for value in edges:
            grades.extend((value - 0.25, value, value + 0.25))
    return grades


def _red_share(edges: ZoneEdges) -> float:
    preview = preview_course_zones(_SEGMENTS, _athlete(edges))
    return float(preview["red_share_of_course_pct"])


@st.composite
def _edge_pair(draw: st.DrawFn) -> tuple[ZoneEdges, ZoneEdges]:
    """A valid line set and one that is componentwise stricter."""

    red_climb = draw(st.integers(12, 40))
    red_descent = draw(st.integers(-40, -16))
    green_max = draw(st.integers(4, min(24, red_climb - 2)))
    green_min = draw(st.integers(max(-24, red_descent + 2), -6))
    strict_green_max = draw(st.integers(4, green_max))
    strict_green_min = draw(st.integers(green_min, -6))
    strict_red_climb = draw(st.integers(max(12, strict_green_max + 2), red_climb))
    strict_red_descent = draw(st.integers(red_descent, min(-16, strict_green_min - 2)))
    loose = _edges(_half(green_min), _half(green_max), _half(red_descent), _half(red_climb))
    strict = _edges(
        _half(strict_green_min),
        _half(strict_green_max),
        _half(strict_red_descent),
        _half(strict_red_climb),
    )
    return loose, strict


def test_omitted_edges_use_the_constants() -> None:
    edges = zone_edges_for(AthleteInput())
    assert edges == _edges(GREEN_MIN_GRADE, GREEN_MAX_GRADE, YELLOW_DOWNHILL_MIN, YELLOW_UPHILL_MAX)
    assert classify_zone(5.0, None, None, None, None) is DigestiveZone.green
    assert classify_zone(5.1) is DigestiveZone.yellow
    assert classify_zone(10.0) is DigestiveZone.yellow
    assert classify_zone(10.1) is DigestiveZone.red
    assert classify_zone(-8.0) is DigestiveZone.green
    assert classify_zone(-12.0) is DigestiveZone.yellow
    assert classify_zone(-12.1) is DigestiveZone.red


def test_sensitive_still_applies_when_green_max_is_omitted() -> None:
    sensitive = zone_edges_for(AthleteInput(climb_sensitivity="sensitive"))
    assert sensitive.green_max == 3.5
    assert sensitive.green_min == GREEN_MIN_GRADE
    assert sensitive.red_climb == YELLOW_UPHILL_MAX
    assert sensitive.red_descent == YELLOW_DOWNHILL_MIN
    climb = course_points([(4_000, 4.0)])
    plan = build_plan(climb, AthleteInput(flat_pace_min_per_km=8.0, climb_sensitivity="sensitive"))
    assert plan.segments[len(plan.segments) // 2].zone is DigestiveZone.yellow


def test_explicit_green_max_overrides_sensitive() -> None:
    edges = zone_edges_for(
        AthleteInput(
            climb_sensitivity="sensitive",
            green_min_grade=-8,
            green_max_grade=5,
            red_descent_grade=-12,
            red_climb_grade=10,
        )
    )
    assert edges.green_max == 5
    climb = course_points([(4_000, 4.0)])
    athlete = _athlete(edges).model_copy(update={"climb_sensitivity": "sensitive", "flat_pace_min_per_km": 8.0})
    plan = build_plan(climb, athlete)
    assert plan.segments[len(plan.segments) // 2].zone is DigestiveZone.green


def test_explicit_default_edges_match_the_omitted_plan() -> None:
    points = course_points([(3_000, -14.0), (4_000, 0.0), (3_000, 16.0)])
    omitted = build_plan(points, AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60))
    explicit = build_plan(points, AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60, **{
        "green_min_grade": -8,
        "green_max_grade": 5,
        "red_descent_grade": -12,
        "red_climb_grade": 10,
    }))
    assert [segment.model_dump() for segment in explicit.segments] == [
        segment.model_dump() for segment in omitted.segments
    ]
    assert [stop.model_dump() for stop in explicit.fuel_stops] == [stop.model_dump() for stop in omitted.fuel_stops]
    assert explicit.totals.model_dump() == omitted.totals.model_dump()
    assert explicit.fuel_budget.model_dump() == omitted.fuel_budget.model_dump()


def test_default_preset_numbers_are_the_constants() -> None:
    assert PRESETS["default"] == (GREEN_MIN_GRADE, GREEN_MAX_GRADE, YELLOW_DOWNHILL_MIN, YELLOW_UPHILL_MAX)


def test_named_presets_keep_red_share_ordered() -> None:
    shares = {name: _red_share(_edges(*numbers)) for name, numbers in PRESETS.items()}
    assert shares["sensitive"] >= shares["default"] >= shares["iron"]


def test_zone_lines_reject_a_missing_yellow_band() -> None:
    with pytest.raises(ValidationError):
        AthleteInput(green_max_grade=12, red_climb_grade=10)
    with pytest.raises(ValidationError):
        AthleteInput(green_min_grade=-12, red_descent_grade=-12)
    with pytest.raises(ValidationError):
        AthleteInput(red_climb_grade=5)
    AthleteInput(climb_sensitivity="sensitive")
    AthleteInput(**{
        "green_min_grade": -8,
        "green_max_grade": 5,
        "red_descent_grade": -12,
        "red_climb_grade": 10,
    })


def test_preview_zones_matches_plan_red_share_without_a_plan() -> None:
    body = {
        "gpx_xml": to_gpx(_MIXED),
        "athlete": {
            "green_min_grade": -6,
            "green_max_grade": 3.5,
            "red_descent_grade": -10,
            "red_climb_grade": 8,
        },
    }
    preview = client.post("/api/preview-zones", json=body)
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert "fuel_stops" not in payload
    assert "segments" not in payload
    plan = client.post("/api/generate-plan", json=body)
    assert plan.status_code == 200, plan.text
    assert payload["red_share_of_course_pct"] == pytest.approx(
        plan.json()["fuel_budget"]["red_share_of_course_pct"]
    )
    direct = preview_zones_json(body, {"races": []}, body["gpx_xml"])
    assert direct["red_share_of_course_pct"] == pytest.approx(payload["red_share_of_course_pct"])
    assert payload["red_share_of_course_pct"] > 0


@settings(max_examples=40, deadline=None, derandomize=True)
@given(pair=_edge_pair())
def test_stricter_thresholds_never_decrease_red_share(pair: tuple[ZoneEdges, ZoneEdges]) -> None:
    loose, strict = pair
    assert zone_edges_for(_athlete(loose)) == loose
    assert zone_edges_for(_athlete(strict)) == strict
    for grade in _probe_grades(loose, strict):
        if _is_red(grade, loose):
            assert _is_red(grade, strict)
    assert _red_share(strict) >= _red_share(loose)
