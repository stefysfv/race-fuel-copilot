"""The fuel budget restates stops, forfeited rows, and red-zone share.

The 70–85% note stays on `fuel_budget.note`. `plan.warnings` still carries
only the under-70% sentence, so older golden snapshots stay valid.
"""

from __future__ import annotations

import math

import pytest

from backend.adapters.catalog import get_race
from backend.adapters.gpx_files import attach_track
from backend.engine.constants import (
    DELIVERY_INFO_RATIO,
    DELIVERY_WARN_RATIO,
    PRE_RACE_QUIET_MIN,
)
from backend.engine.scheduler import generate_plan, generate_plan_for_race
from backend.engine.short_race import _is_short_race
from backend.engine.totals_warnings import (
    _active_fuel_minutes,
    _delivered_grams_per_hour,
    _delivery_warnings,
    _fuel_budget,
)
from backend.models import (
    AthleteInput,
    DeliveryTier,
    DigestiveZone,
    ForfeitedDose,
    FuelForm,
    FuelStop,
    RacePlan,
    ReasonCode,
    Segment,
    TrackPoint,
)

METERS_PER_DEG_LAT = 6_371_000.0 * math.pi / 180.0


def assert_fuel_budget(plan: RacePlan) -> None:
    """Every budget field can be rebuilt from the plan the engine already stored."""

    budget = plan.fuel_budget
    finish = plan.clock_start_min + plan.predicted_duration_min
    active = _active_fuel_minutes(plan.clock_start_min, finish)
    assert budget.target_g_per_h == pytest.approx(plan.athlete.carbs_g_per_hour)
    delivered_rate = _delivered_grams_per_hour(plan.fuel_stops, plan.clock_start_min, finish)
    assert budget.delivered_g_per_h == pytest.approx(delivered_rate)
    assert budget.delivered_g_per_h == pytest.approx(plan.totals.carbs_g_per_hour_delivered)
    if active <= 0:
        assert budget.delivered_total_g == 0
        assert budget.target_total_g == 0
    else:
        delivered = sum(
            stop.carbs_g for stop in plan.fuel_stops if stop.scheduled_minute >= PRE_RACE_QUIET_MIN
        )
        assert budget.delivered_total_g == pytest.approx(delivered)
        assert budget.target_total_g == pytest.approx(budget.target_g_per_h * active / 60.0)
        assert budget.delivered_g_per_h == pytest.approx(delivered / (active / 60.0))
    if budget.target_g_per_h > 0:
        assert budget.delivery_ratio == pytest.approx(budget.delivered_g_per_h / budget.target_g_per_h)
    else:
        assert budget.delivery_ratio == 0
    grouped: dict[str, float] = {}
    for item in plan.forfeited:
        grouped[item.code.value] = grouped.get(item.code.value, 0.0) + item.carbs_g
    assert budget.forfeited_total_g == pytest.approx(sum(grouped.values()))
    assert set(budget.forfeited_by_code) == {code for code, grams in grouped.items() if grams > 0}
    for code, grams in budget.forfeited_by_code.items():
        assert grams > 0
        assert grams == pytest.approx(grouped[code])
    distance = sum(segment.distance_m for segment in plan.segments)
    minutes = sum(segment.predicted_minutes for segment in plan.segments)
    red_distance = sum(segment.distance_m for segment in plan.segments if segment.zone is DigestiveZone.red)
    red_minutes = sum(
        segment.predicted_minutes for segment in plan.segments if segment.zone is DigestiveZone.red
    )
    if distance > 0:
        assert budget.red_share_of_course_pct == pytest.approx(min(100.0, 100.0 * red_distance / distance))
    if minutes > 0:
        assert budget.red_share_of_time_pct == pytest.approx(min(100.0, 100.0 * red_minutes / minutes))
    assert not any("under 85%" in warning for warning in plan.warnings)
    if _is_short_race(plan.athlete):
        assert budget.tier is DeliveryTier.ok
        assert budget.note == ""
        assert not any("below 70%" in warning for warning in plan.warnings)
        return
    if budget.tier is DeliveryTier.warn:
        assert budget.note == ""
        assert any("below 70%" in warning for warning in plan.warnings)
        assert budget.delivery_ratio < DELIVERY_WARN_RATIO
    elif budget.tier is DeliveryTier.info:
        assert "under 85%" in budget.note
        assert not any("below 70%" in warning for warning in plan.warnings)
        assert DELIVERY_WARN_RATIO <= budget.delivery_ratio < DELIVERY_INFO_RATIO
    else:
        assert budget.tier is DeliveryTier.ok
        assert budget.note == ""
        assert not any("below 70%" in warning for warning in plan.warnings)
        assert budget.delivery_ratio >= DELIVERY_INFO_RATIO


def _profile(length_m: float, grade: float, elevation_m: float = 1200.0) -> list[TrackPoint]:
    """Walk north. One grade for the whole track."""

    lat, lon, elevation = 42.6, 23.3, elevation_m
    step = 200.0
    points = [TrackPoint(lat=lat, lon=lon, elevation_m=elevation)]
    steps = max(1, int(round(length_m / step)))
    step_len = length_m / steps
    dlat = step_len / METERS_PER_DEG_LAT
    rise = (grade / 100.0) * step_len
    for _ in range(steps):
        lat += dlat
        elevation += rise
        points.append(TrackPoint(lat=lat, lon=lon, elevation_m=elevation))
    return points


def _plan(length_m: float, grade: float, athlete: AthleteInput, elevation_m: float = 1200.0) -> RacePlan:
    return generate_plan(
        race_id="budget",
        race_name="Budget course",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=_profile(length_m, grade, elevation_m),
        athlete=athlete,
    )


def _segment(
    index: int,
    start_m: float,
    end_m: float,
    start_min: float,
    end_min: float,
    zone: DigestiveZone,
) -> Segment:
    distance = end_m - start_m
    minutes = end_min - start_min
    pace = minutes / (distance / 1000.0)
    return Segment(
        index=index,
        start_distance_m=start_m,
        end_distance_m=end_m,
        distance_m=distance,
        start_lat=42.6,
        start_lon=23.3,
        end_lat=42.61,
        end_lon=23.3,
        elevation_start_m=1000,
        elevation_end_m=1000,
        elevation_gain_m=0,
        elevation_loss_m=0,
        grade_percent=15 if zone is DigestiveZone.red else 0,
        zone=zone,
        cost_factor=1,
        predicted_minutes=minutes,
        start_minute=start_min,
        end_minute=end_min,
        predicted_pace_min_per_km=pace,
        gap_min_per_km=pace,
    )


def _stop(minute: float, carbs_g: float) -> FuelStop:
    return FuelStop(
        sequence=1,
        ideal_minute=minute,
        scheduled_minute=minute,
        shift_minutes=0,
        distance_km=4,
        latitude=42.6,
        longitude=23.3,
        segment_index=0,
        grade_percent=0,
        zone=DigestiveZone.green,
        carbs_g=carbs_g,
        fluid_ml=0,
        sodium_mg=0,
        form=FuelForm.gel,
        reason="test feed",
    )


def _window(carbs_g: float, athlete: AthleteInput, forfeited: list[ForfeitedDose] | None = None):
    """Two hours after the quiet minute. `carbs_g` is the only counted dose."""

    segments = [
        _segment(0, 0, 2_000, 0, 40, DigestiveZone.red),
        _segment(1, 2_000, 10_000, 40, 170, DigestiveZone.green),
    ]
    stops = [_stop(20, 40), _stop(80, carbs_g)]
    budget = _fuel_budget(
        stops,
        forfeited or [],
        segments,
        athlete,
        start_minute=0,
        finish_minute=170,
    )
    notes = _delivery_warnings(stops, [], segments, athlete, budget.delivered_g_per_h)
    return budget, notes


def test_budget_matches_real_stops_and_forfeited_rows() -> None:
    """Flat, red, and short plans each rebuild from their own stops."""

    flat = _plan(20_000, 0.0, AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60))
    red = _plan(8_000, 15.0, AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60))
    short = _plan(12_000, 0.0, AthleteInput(target_time_minutes=90))
    assert_fuel_budget(flat)
    assert_fuel_budget(red)
    assert_fuel_budget(short)
    assert "SKIPPED_RED_ZONE" in red.fuel_budget.forfeited_by_code
    assert red.fuel_budget.red_share_of_course_pct > 50
    assert short.fuel_budget.forfeited_total_g == 0
    assert short.fuel_budget.tier is DeliveryTier.ok
    assert short.forfeited == []


def test_info_band_stays_off_the_warning_list() -> None:
    """70% up to 85% is a note. Under 70% is still the warning. 85% is quiet."""

    athlete = AthleteInput(carbs_g_per_hour=60)
    info, info_notes = _window(90, athlete)
    assert info.delivered_g_per_h == pytest.approx(45)
    assert info.delivered_total_g == pytest.approx(90)
    assert info.target_total_g == pytest.approx(120)
    assert info.delivery_ratio == pytest.approx(0.75)
    assert info.red_share_of_course_pct == pytest.approx(20)
    assert info.red_share_of_time_pct == pytest.approx(100.0 * 40 / 170)
    assert info.tier is DeliveryTier.info
    assert "under 85%" in info.note
    assert info_notes == []

    on_the_warn_line, warn_line_notes = _window(84, athlete)
    assert on_the_warn_line.delivery_ratio == pytest.approx(0.70)
    assert on_the_warn_line.tier is DeliveryTier.info
    assert warn_line_notes == []

    on_the_info_line, info_line_notes = _window(102, athlete)
    assert on_the_info_line.delivery_ratio == pytest.approx(0.85)
    assert on_the_info_line.tier is DeliveryTier.ok
    assert on_the_info_line.note == ""
    assert info_line_notes == []

    low, low_notes = _window(30, athlete)
    assert low.tier is DeliveryTier.warn
    assert low.note == ""
    assert any("below 70%" in note for note in low_notes)
    assert not any("under 85%" in note for note in low_notes)

    short_budget, short_notes = _window(30, AthleteInput(carbs_g_per_hour=60, target_time_minutes=90))
    assert short_budget.tier is DeliveryTier.ok
    assert short_budget.note == ""
    assert short_notes == []


def test_forfeited_codes_sum_to_the_total() -> None:
    dropped = [
        ForfeitedDose(at_min=30, carbs_g=15, code=ReasonCode.QUIET_WINDOW),
        ForfeitedDose(at_min=80, carbs_g=12, code=ReasonCode.SKIPPED_RED_ZONE),
        ForfeitedDose(at_min=100, carbs_g=3, code=ReasonCode.SKIPPED_RED_ZONE),
    ]
    budget, _notes = _window(90, AthleteInput(carbs_g_per_hour=60), dropped)
    assert budget.forfeited_by_code == {"QUIET_WINDOW": 15, "SKIPPED_RED_ZONE": 15}
    assert budget.forfeited_total_g == pytest.approx(30)


def test_default_vitosha_40_info_stays_off_warnings() -> None:
    """The catalog default is about 71.7% of target, so it must not gain a warning."""

    plan = generate_plan_for_race(attach_track(get_race("salomon-vitosha-40")), AthleteInput())
    assert_fuel_budget(plan)
    budget = plan.fuel_budget
    assert budget.delivery_ratio == pytest.approx(0.717, abs=0.01)
    assert DELIVERY_WARN_RATIO <= budget.delivery_ratio < DELIVERY_INFO_RATIO
    assert budget.tier is DeliveryTier.info
    assert "under 85%" in budget.note
    assert plan.warnings == []
