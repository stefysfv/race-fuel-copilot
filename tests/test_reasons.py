"""One scenario per reason code, plus reason snapshots.

Existing API goldens ignore `reasons` and `forfeited`. These files keep only
those fields.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.engine.scheduler import generate_plan, schedule_fuel
from backend.gpx_parser import EARTH_RADIUS_M, CoursePoint, location_at_distance, prepare_course
from backend.models import (
    AidStation,
    AthleteInput,
    CustomFoodItem,
    DigestiveZone,
    PackedVestItem,
    RacePlan,
    ReasonCode,
    Segment,
    TrackPoint,
)

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
FLOAT_DECIMALS = 6
METERS_PER_DEG = EARTH_RADIUS_M * math.pi / 180.0
STEP_M = 200.0


def _course(sections: list[tuple[float, float]], elevation: float = 1800.0) -> list[TrackPoint]:
    """Walk north. Each section is (metres, grade percent)."""

    lat, lon = 42.6, 23.3
    points = [TrackPoint(lat=lat, lon=lon, elevation_m=elevation)]
    for length_m, grade in sections:
        steps = max(1, int(round(length_m / STEP_M)))
        step_len = length_m / steps
        rise = (grade / 100.0) * step_len
        dlat = step_len / METERS_PER_DEG
        for _ in range(steps):
            lat += dlat
            elevation += rise
            points.append(TrackPoint(lat=lat, lon=lon, elevation_m=elevation))
    return points


def _plan(profile: list[TrackPoint], athlete: AthleteInput | None = None, **kwargs: Any) -> RacePlan:
    stations = kwargs.pop("aid_stations", [])
    return generate_plan(
        race_id="reasons",
        race_name="reasons",
        declared_distance_km=0.0,
        aid_stations=stations,
        profile=profile,
        athlete=athlete or AthleteInput(),
        **kwargs,
    )


def _stop_codes(plan: RacePlan) -> set[ReasonCode]:
    return {reason.code for stop in plan.fuel_stops for reason in stop.reasons}


def _drop_codes(plan: RacePlan) -> set[ReasonCode]:
    return {item.code for item in plan.forfeited}


def _assert_positive_forfeits(plan: RacePlan) -> None:
    assert all(item.carbs_g > 0 for item in plan.forfeited)


def test_shifted_earlier() -> None:
    """Flat ground ends just before minute 30, so that feed steps back onto it."""

    plan = _plan(_course([(3_600.0, 0.0), (4_000.0, 15.0)]))
    _assert_positive_forfeits(plan)
    assert ReasonCode.SHIFTED_EARLIER in _stop_codes(plan)


def test_shifted_later() -> None:
    """A steep start gives way to flat ground inside the forward window."""

    plan = _plan(_course([(2_200.0, 15.0), (5_000.0, 0.0)], elevation=1200.0))
    _assert_positive_forfeits(plan)
    assert ReasonCode.SHIFTED_LATER in _stop_codes(plan)


def test_yellow_half() -> None:
    plan = _plan(_course([(12_000.0, 8.0)], elevation=1200.0))
    _assert_positive_forfeits(plan)
    assert ReasonCode.YELLOW_HALF in _stop_codes(plan)
    assert ReasonCode.YELLOW_HALF in _drop_codes(plan)


def test_skipped_red_zone() -> None:
    plan = _plan(_course([(8_000.0, 15.0)], elevation=1200.0))
    _assert_positive_forfeits(plan)
    assert ReasonCode.SKIPPED_RED_ZONE in _stop_codes(plan)
    assert ReasonCode.SKIPPED_RED_ZONE in _drop_codes(plan)


def test_gi_rescue_nil_is_a_stop_not_a_zero_forfeit() -> None:
    plan = _plan(_course([(30_000.0, 0.0)]), rescue_start_min=80)
    _assert_positive_forfeits(plan)
    assert ReasonCode.GI_RESCUE_NIL in _stop_codes(plan)
    assert ReasonCode.GI_RESCUE_NIL not in _drop_codes(plan)


def test_gi_rescue_ease_and_half() -> None:
    plan = _plan(_course([(30_000.0, 0.0)]), rescue_start_min=80)
    _assert_positive_forfeits(plan)
    assert ReasonCode.GI_RESCUE_EASE in _stop_codes(plan)
    assert ReasonCode.GI_RESCUE_HALF in _stop_codes(plan)
    assert ReasonCode.GI_RESCUE_HALF in _drop_codes(plan)


def test_gi_rescue_red() -> None:
    plan = _plan(_course([(10_000.0, 15.0)], elevation=1200.0), rescue_start_min=70)
    _assert_positive_forfeits(plan)
    assert ReasonCode.GI_RESCUE_RED in _stop_codes(plan)
    assert ReasonCode.GI_RESCUE_RED in _drop_codes(plan)


def test_quiet_window() -> None:
    plan = _plan(_course([(16_000.0, 0.0)]))
    _assert_positive_forfeits(plan)
    assert ReasonCode.QUIET_WINDOW in _stop_codes(plan)
    assert ReasonCode.QUIET_WINDOW in _drop_codes(plan)


def test_descent_lock() -> None:
    plan = _plan(_course([(20_000.0, -10.0)], elevation=3500.0))
    _assert_positive_forfeits(plan)
    assert ReasonCode.DESCENT_LOCK in _stop_codes(plan)
    assert ReasonCode.DESCENT_LOCK in _drop_codes(plan)


def test_aid_restock_and_covered() -> None:
    profile = _course([(16_000.0, 0.0)])
    course = prepare_course(profile)
    point = location_at_distance(course, 8_000.0)
    station = AidStation(
        name="Table",
        distance_km=point.distance_m / 1000.0,
        lat=point.lat,
        lon=point.lon,
        elevation_m=point.elevation_m,
        has_food=True,
    )
    plan = _plan(profile, aid_stations=[station])
    _assert_positive_forfeits(plan)
    assert ReasonCode.AID_RESTOCK in _stop_codes(plan)
    assert ReasonCode.AID_COVERED in _stop_codes(plan)
    assert ReasonCode.AID_RESTOCK not in _drop_codes(plan)
    assert ReasonCode.AID_COVERED not in _drop_codes(plan)


def test_opening_solid_and_savoury_slot() -> None:
    plan = _plan(_course([(30_000.0, 0.0)]))
    _assert_positive_forfeits(plan)
    assert ReasonCode.OPENING_SOLID in _stop_codes(plan)
    assert ReasonCode.SAVOURY_SLOT in _stop_codes(plan)


def test_solid_slot() -> None:
    """A feed in the gap after the opening solid and before the savoury clock."""

    athlete = AthleteInput(feed_interval_min=18)
    plan = _plan(_course([(22_000.0, 0.0)]), athlete)
    _assert_positive_forfeits(plan)
    assert ReasonCode.SOLID_SLOT in _stop_codes(plan)


def test_portion() -> None:
    athlete = AthleteInput(
        vest_foods=["gel_25"],
        custom_vest_items=[CustomFoodItem(key="gel_25", name="Mine", carbs_g=22)],
    )
    plan = _plan(_course([(16_000.0, 0.0)]), athlete)
    _assert_positive_forfeits(plan)
    assert ReasonCode.PORTION in _stop_codes(plan)


def test_no_vest_food() -> None:
    plan = _plan(_course([(12_000.0, 0.0)]), AthleteInput(vest_foods=[]))
    _assert_positive_forfeits(plan)
    assert ReasonCode.NO_VEST_FOOD in _stop_codes(plan)
    assert ReasonCode.NO_VEST_FOOD in _drop_codes(plan)


def test_no_climb_food() -> None:
    plan = _plan(_course([(12_000.0, 8.0)], elevation=1200.0), AthleteInput(vest_foods=[]))
    _assert_positive_forfeits(plan)
    assert ReasonCode.NO_CLIMB_FOOD in _stop_codes(plan)
    assert ReasonCode.NO_CLIMB_FOOD in _drop_codes(plan)


def test_packed_vest() -> None:
    item = PackedVestItem(id="gel", name="Packed gel", carbs_g=25, qty=1, form="gel")
    plan = _plan(_course([(16_000.0, 0.0)]), packed_vest_items=[item])
    _assert_positive_forfeits(plan)
    assert ReasonCode.PACKED_VEST in _stop_codes(plan)


def test_short_race_bite_and_sip() -> None:
    plan = _plan(_course([(12_000.0, 0.0)]), AthleteInput(target_time_minutes=90))
    _assert_positive_forfeits(plan)
    assert ReasonCode.SHORT_RACE_BITE in _stop_codes(plan)
    assert ReasonCode.SHORT_RACE_SIP in _stop_codes(plan)
    assert plan.forfeited == []


def test_unplaced_window() -> None:
    """A clock gap wider than ±6 min drops the slot that lands in it."""

    segments = [
        _segment(0, 0.0, 1_000.0, 0.0, 10.0),
        _segment(1, 1_000.0, 3_000.0, 40.0, 80.0),
    ]
    course = [
        CoursePoint(lat=42.6, lon=23.3, elevation_m=1000.0, distance_m=0.0),
        CoursePoint(lat=42.62, lon=23.3, elevation_m=1000.0, distance_m=3_000.0),
    ]
    stops, forfeited = schedule_fuel(segments, course, AthleteInput())
    assert stops
    assert any(item.code is ReasonCode.UNPLACED and item.carbs_g > 0 for item in forfeited)
    assert all(item.carbs_g > 0 for item in forfeited)


def _segment(
    index: int,
    start_m: float,
    end_m: float,
    start_min: float,
    end_min: float,
) -> Segment:
    minutes = end_min - start_min
    distance = end_m - start_m
    return Segment(
        index=index,
        start_distance_m=start_m,
        end_distance_m=end_m,
        distance_m=distance,
        start_lat=42.6,
        start_lon=23.3,
        end_lat=42.61,
        end_lon=23.3,
        elevation_start_m=1000.0,
        elevation_end_m=1000.0,
        elevation_gain_m=0.0,
        elevation_loss_m=0.0,
        grade_percent=0.0,
        zone=DigestiveZone.green,
        cost_factor=1.0,
        predicted_minutes=minutes,
        start_minute=start_min,
        end_minute=end_min,
        predicted_pace_min_per_km=minutes / (distance / 1000.0),
        gap_min_per_km=minutes / (distance / 1000.0),
    )


def _reason_view(plan: RacePlan) -> dict[str, Any]:
    body = plan.model_dump(mode="json")
    return {
        "stops": [
            {
                "sequence": stop["sequence"],
                "ideal_minute": stop["ideal_minute"],
                "scheduled_minute": stop["scheduled_minute"],
                "carbs_g": stop["carbs_g"],
                "reasons": stop["reasons"],
            }
            for stop in body["fuel_stops"]
        ],
        "forfeited": body["forfeited"],
    }


def _round_numbers(value: Any) -> Any:
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


def _assert_reason_golden(name: str, plan: RacePlan, update: bool) -> None:
    actual = _round_numbers(_reason_view(plan))
    path = GOLDEN_DIR / name
    if update:
        path.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return
    assert path.is_file(), f"Missing reason snapshot {path}. Re-run with --update-golden."
    expected = json.loads(path.read_text(encoding="utf-8"))
    assert actual == expected


def test_reason_golden_flat(update_golden: bool) -> None:
    _assert_reason_golden("reasons__flat-30km.json", _plan(_course([(30_000.0, 0.0)])), update_golden)


def test_reason_golden_rescue(update_golden: bool) -> None:
    plan = _plan(_course([(30_000.0, 0.0)]), rescue_start_min=80)
    _assert_reason_golden("reasons__rescue.json", plan, update_golden)


def test_reason_golden_short_race(update_golden: bool) -> None:
    plan = _plan(_course([(12_000.0, 0.0)]), AthleteInput(target_time_minutes=90))
    _assert_reason_golden("reasons__short-race.json", plan, update_golden)


def test_reason_golden_aid_plate(update_golden: bool) -> None:
    profile = _course([(16_000.0, 0.0)])
    course = prepare_course(profile)
    point = location_at_distance(course, 8_000.0)
    station = AidStation(
        name="Table",
        distance_km=point.distance_m / 1000.0,
        lat=point.lat,
        lon=point.lon,
        elevation_m=point.elevation_m,
        has_food=True,
    )
    _assert_reason_golden(
        "reasons__aid-plate.json",
        _plan(profile, aid_stations=[station]),
        update_golden,
    )
