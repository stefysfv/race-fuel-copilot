"""Hypothesis tests for the approved fuel-plan invariants.

Numeric draws read the bounds off the Pydantic fields in `backend/models.py`.
The four bundled GPX courses are included, plus synthetic flat, all-climb, and
sawtooth profiles. The logged examples in `FINDINGS.md` are covered by
`test_logged_*`. This file does not change plan output.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass

import annotated_types
import pytest
from hypothesis import HealthCheck, assume, given, settings, strategies as st
from pydantic import BaseModel

from backend.adapters.catalog import load_race_catalog
from backend.adapters.gpx_files import attach_track
from backend.engine.constants import (
    AID_LEG_MIN_MINUTES,
    CAFFEINE_MG_PER_KG,
    EARLY_SAVOURY_MIN,
    MAX_PACE_RATIO,
    MIN_PACE_RATIO,
    MIN_PACE_SAMPLE_MIN,
    PACK_BUFFER,
    DELIVERY_INFO_RATIO,
    DELIVERY_WARN_RATIO,
    PRE_RACE_QUIET_MIN,
    RED_MICRO_DOSE_G,
    RESCUE_COOLDOWN_MIN,
    RESCUE_EASE_CARBS_G,
    RESCUE_GENTLE_FEEDS,
    RESCUE_NIL_CARB_MIN,
    SAVOURY_START_MIN,
    SHORT_RACE_NOTE,
    SOLID_EVERY_MIN,
    WINDOW_MIN,
    YELLOW_DOWNHILL_MIN,
    YELLOW_UPHILL_MAX,
)
from backend.engine.gap import gap_cost_factor
from backend.engine.recalibrate import recalibrate, snap_to_track
from backend.engine.reasons import SHIFT_EPSILON_MIN
from backend.engine.scheduler import generate_plan
from backend.engine.short_race import _is_short_race
from backend.engine.sweat import sweat_rates
from backend.engine.totals_warnings import _active_fuel_minutes, _delivered_grams_per_hour
from backend.engine.zones import classify_zone, zone_edges_for
from backend.gpx_parser import EARTH_RADIUS_M, location_at_distance, prepare_course
from backend.models import (
    AidStation,
    AthleteInput,
    CustomAidInput,
    CustomFoodItem,
    DeliveryTier,
    DigestiveZone,
    FuelForm,
    FuelStop,
    GeneratePlanRequest,
    LockerItem,
    PackedVestItem,
    Race,
    RacePlan,
    TrackPoint,
)

# Steeper than the red lines so a linear synthetic climb or descent stays red
# after the 500 m slice. 15% matches the red-window example in the unit tests.
CLIMB_GRADE = YELLOW_UPHILL_MAX + 5.0
DESCENT_GRADE = YELLOW_DOWNHILL_MIN - 3.0
METERS_PER_DEG_LAT = EARTH_RADIUS_M * math.pi / 180.0
# Repeated `lat += dlat` can land a few ulps past a start that sits exactly on
# 90 - distance/METERS_PER_DEG_LAT. Keep the drawn origin under that line.
LAT_WALK_MARGIN_DEG = 1e-4
STEP_M = 200.0
TOOTH_M = 1_000.0

BUNDLED_IDS = (
    "salomon-vitosha-12",
    "salomon-vitosha-27",
    "salomon-vitosha-40",
    "vitosha-100",
)
SYNTHETIC_KINDS = ("flat", "all-climb", "sawtooth")
COURSE_KINDS = SYNTHETIC_KINDS + BUNDLED_IDS

# Client clamp from frontend/build_plan.js. Inside AthleteInput's open pace interval.
CLIENT_PACE_MIN = 3.51
CLIENT_PACE_MAX = 19.9

FAST = settings(max_examples=20, deadline=None, derandomize=True)
SLOW = settings(
    max_examples=8,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)

_BUNDLED: dict[str, Race] | None = None


def bundled_races() -> dict[str, Race]:
    """Load the four catalog GPX courses once."""

    global _BUNDLED
    if _BUNDLED is None:
        loaded = {race.id: attach_track(race) for race in load_race_catalog()}
        missing = [race_id for race_id in BUNDLED_IDS if race_id not in loaded]
        if missing:
            raise RuntimeError(f"Bundled courses missing: {missing}")
        _BUNDLED = {race_id: loaded[race_id] for race_id in BUNDLED_IDS}
    return _BUNDLED


def _bounds(model: type[BaseModel], name: str) -> tuple[float, float | None, bool, bool]:
    """Read ge/gt/le/lt off one model field. Upper bound is None when the field has none."""

    low: float | None = None
    high: float | None = None
    exclude_min = False
    exclude_max = False
    for meta in model.model_fields[name].metadata:
        if isinstance(meta, annotated_types.Gt):
            low, exclude_min = float(meta.gt), True
        elif isinstance(meta, annotated_types.Ge):
            low, exclude_min = float(meta.ge), False
        elif isinstance(meta, annotated_types.Lt):
            high, exclude_max = float(meta.lt), True
        elif isinstance(meta, annotated_types.Le):
            high, exclude_max = float(meta.le), False
    if low is None:
        raise KeyError(f"{model.__name__}.{name} has no lower bound")
    return low, high, exclude_min, exclude_max


def _floats(model: type[BaseModel], name: str, *, upper: float | None = None) -> st.SearchStrategy[float]:
    low, high, exclude_min, exclude_max = _bounds(model, name)
    if high is None:
        if upper is None:
            raise KeyError(f"{model.__name__}.{name} has no upper bound")
        high = upper
        exclude_max = False
    return st.floats(
        min_value=low,
        max_value=high,
        exclude_min=exclude_min,
        exclude_max=exclude_max,
        allow_nan=False,
        allow_infinity=False,
        allow_subnormal=False,
    )


def _ints(model: type[BaseModel], name: str) -> st.SearchStrategy[int]:
    low, high, _, _ = _bounds(model, name)
    if high is None:
        raise KeyError(f"{model.__name__}.{name} has no upper bound")
    return st.integers(min_value=math.ceil(low), max_value=math.floor(high))


def _choices(model: type[BaseModel], name: str) -> st.SearchStrategy[str]:
    for meta in model.model_fields[name].metadata:
        pattern = getattr(meta, "pattern", None)
        if isinstance(pattern, str):
            body = pattern[2:-2] if pattern.startswith("^(") and pattern.endswith(")$") else pattern
            return st.sampled_from(body.split("|"))
    raise KeyError(f"{model.__name__}.{name} has no pattern")


def _max_len(model: type[BaseModel], name: str) -> int:
    for meta in model.model_fields[name].metadata:
        if isinstance(meta, annotated_types.MaxLen):
            return int(meta.max_length)
    raise KeyError(f"{model.__name__}.{name} has no max length")


def _text(model: type[BaseModel], name: str, *, fallback_max: int | None = None) -> st.SearchStrategy[str]:
    min_size = 0
    max_size = fallback_max
    for meta in model.model_fields[name].metadata:
        if isinstance(meta, annotated_types.MinLen):
            min_size = int(meta.min_length)
        elif isinstance(meta, annotated_types.MaxLen):
            max_size = int(meta.max_length)
    if max_size is None:
        raise KeyError(f"{model.__name__}.{name} has no max length")
    return st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=min_size, max_size=max_size)


def _optional_floats(model: type[BaseModel], name: str) -> st.SearchStrategy[float | None]:
    return st.one_of(st.none(), _floats(model, name))


def _portion_grams(model: type[BaseModel], name: str) -> st.SearchStrategy[float]:
    """Carb portions inside the field, from one microgram up to the maximum.

    The field is `gt=0, le=120`. The packer turns a non-finite quotient into
    zero pieces, so a subnormal portion cannot raise OverflowError. Draws
    stay at or above one microgram so a portion is still a real bite.
    """

    _, high, _, exclude_max = _bounds(model, name)
    assert high is not None
    return st.floats(
        min_value=1e-6,
        max_value=high,
        exclude_max=exclude_max,
        allow_nan=False,
        allow_infinity=False,
        allow_subnormal=False,
    )


@st.composite
def locker_items(draw: st.DrawFn) -> LockerItem:
    return LockerItem(
        name=draw(_text(LockerItem, "name")),
        carbs_g=draw(_portion_grams(LockerItem, "carbs_g")),
        caffeine_mg=draw(_floats(LockerItem, "caffeine_mg")),
        kind=draw(_choices(LockerItem, "kind")),
    )


@st.composite
def custom_foods(draw: st.DrawFn) -> CustomFoodItem:
    return CustomFoodItem(
        key=draw(_text(CustomFoodItem, "key")),
        name=draw(_text(CustomFoodItem, "name")),
        carbs_g=draw(_portion_grams(CustomFoodItem, "carbs_g")),
        sodium_mg=draw(_floats(CustomFoodItem, "sodium_mg")),
        flavor=draw(_choices(CustomFoodItem, "flavor")),
    )


@st.composite
def packed_items(draw: st.DrawFn) -> PackedVestItem:
    return PackedVestItem(
        id=draw(_text(PackedVestItem, "id")),
        name=draw(_text(PackedVestItem, "name")),
        carbs_g=draw(_portion_grams(PackedVestItem, "carbs_g")),
        sodium_mg=draw(_floats(PackedVestItem, "sodium_mg")),
        caffeine_mg=draw(_floats(PackedVestItem, "caffeine_mg")),
        qty=draw(_ints(PackedVestItem, "qty")),
        flavor=draw(_choices(PackedVestItem, "flavor")),
        form=draw(_choices(PackedVestItem, "form")),
    )


@st.composite
def athletes(draw: st.DrawFn) -> AthleteInput:
    """One athlete inside every AthleteInput bound. Vest keys are menu keys plus one unknown."""

    from backend.engine.doses_menu import STATION_MENU, VEST_MENU

    vest_keys = tuple(VEST_MENU)
    station_keys = tuple(STATION_MENU)
    return AthleteInput(
        weight_kg=draw(_floats(AthleteInput, "weight_kg")),
        temperature_c=draw(_floats(AthleteInput, "temperature_c")),
        flat_pace_min_per_km=draw(_floats(AthleteInput, "flat_pace_min_per_km")),
        carbs_g_per_hour=draw(_floats(AthleteInput, "carbs_g_per_hour")),
        fluid_ml_per_hour=draw(_floats(AthleteInput, "fluid_ml_per_hour")),
        sodium_mg_per_hour=draw(_floats(AthleteInput, "sodium_mg_per_hour")),
        feed_interval_min=draw(_ints(AthleteInput, "feed_interval_min")),
        fuel_preference=draw(_choices(AthleteInput, "fuel_preference")),
        locker=draw(st.lists(locker_items(), max_size=_max_len(AthleteInput, "locker"))),
        vest_foods=draw(
            st.lists(
                st.sampled_from((*vest_keys, "not-a-food")),
                max_size=len(vest_keys),
                unique=True,
            )
        ),
        station_foods=draw(
            st.lists(st.sampled_from(station_keys), max_size=len(station_keys), unique=True)
        ),
        custom_vest_items=draw(
            st.lists(custom_foods(), max_size=_max_len(AthleteInput, "custom_vest_items"))
        ),
        custom_station_items=draw(
            st.lists(custom_foods(), max_size=_max_len(AthleteInput, "custom_station_items"))
        ),
        carb_multiplier=draw(_floats(AthleteInput, "carb_multiplier")),
        climb_sensitivity=draw(_choices(AthleteInput, "climb_sensitivity")),
        early_savoury=draw(st.booleans()),
        target_time_minutes=draw(_optional_floats(AthleteInput, "target_time_minutes")),
        has_salt_caps=draw(st.booleans()),
        salt_caps_qty=draw(_ints(AthleteInput, "salt_caps_qty")),
        salt_cap_mg=draw(_floats(AthleteInput, "salt_cap_mg")),
        flask_count=draw(_ints(AthleteInput, "flask_count")),
    )


@dataclass
class StationDraw:
    name: str
    distance_km: float
    has_water: bool
    has_food: bool
    cutoff_hours: float | None


@dataclass
class PlanCase:
    """A drawn input small enough to reprint when a property fails."""

    label: str
    athlete: AthleteInput
    stations: list[StationDraw]
    packed: list[PackedVestItem]
    distance_km: float | None = None
    origin_lat: float = 42.6
    origin_lon: float = 23.3
    origin_elevation_m: float = 1200.0

    def __repr__(self) -> str:
        packed = [item.model_dump() for item in self.packed]
        return (
            f"PlanCase(label={self.label!r}, distance_km={self.distance_km!r}, "
            f"origin=({self.origin_lat!r}, {self.origin_lon!r}, {self.origin_elevation_m!r}), "
            f"athlete={self.athlete.model_dump()!r}, stations={self.stations!r}, packed={packed!r})"
        )


def _max_fit_km(kind: str) -> float:
    """Race.distance_km capped so an all-climb profile stays inside TrackPoint.elevation_m."""

    _, high, _, _ = _bounds(Race, "distance_km")
    assert high is not None
    if kind != "all-climb":
        return high
    low_e, high_e, _, _ = _bounds(TrackPoint, "elevation_m")
    assert high_e is not None
    span = (high_e - low_e) - 1.0
    fit_km = span / (CLIMB_GRADE / 100.0) / 1000.0
    return min(high, fit_km)


def build_profile(
    kind: str,
    distance_km: float,
    lat: float,
    lon: float,
    elevation_m: float,
) -> list[TrackPoint]:
    """Walk north. `kind` is flat, all-climb, or sawtooth."""

    distance_m = distance_km * 1000.0
    if kind == "flat":
        sections = [(distance_m, 0.0)]
    elif kind == "all-climb":
        sections = [(distance_m, CLIMB_GRADE)]
    elif kind == "sawtooth":
        sections = []
        left = distance_m
        climbing = True
        while left > 0.5:
            step = min(TOOTH_M, left)
            sections.append((step, CLIMB_GRADE if climbing else DESCENT_GRADE))
            left -= step
            climbing = not climbing
        if not sections:
            sections = [(distance_m, CLIMB_GRADE)]
    else:
        raise ValueError(f"Unknown synthetic profile {kind}")

    points = [TrackPoint(lat=lat, lon=lon, elevation_m=elevation_m)]
    for length_m, grade in sections:
        steps = max(1, int(round(length_m / STEP_M)))
        step_len = length_m / steps
        rise = (grade / 100.0) * step_len
        dlat = step_len / METERS_PER_DEG_LAT
        for _ in range(steps):
            lat += dlat
            elevation_m += rise
            points.append(TrackPoint(lat=lat, lon=lon, elevation_m=elevation_m))
    return points


def _path_stays_inside(kind: str, distance_km: float, elevation_m: float) -> bool:
    elevation = elevation_m
    low_e, high_e, _, _ = _bounds(TrackPoint, "elevation_m")
    assert high_e is not None
    if kind == "flat":
        return low_e < elevation < high_e
    if kind == "all-climb":
        elevation += distance_km * 1000.0 * CLIMB_GRADE / 100.0
        return low_e < elevation_m < high_e and low_e < elevation < high_e
    # Sawtooth oscillates by one tooth.
    swing = TOOTH_M * max(CLIMB_GRADE, abs(DESCENT_GRADE)) / 100.0
    return low_e + swing < elevation_m < high_e - swing


@st.composite
def station_draws(draw: st.DrawFn) -> StationDraw:
    # AidStation.distance_km is only ge=0. Typed stations, which are the request
    # body, stop at CustomAidInput's 500 km cap.
    return StationDraw(
        name=draw(_text(CustomAidInput, "name")),
        distance_km=draw(_floats(CustomAidInput, "distance_km")),
        has_water=draw(st.booleans()),
        has_food=draw(st.booleans()),
        cutoff_hours=draw(_optional_floats(CustomAidInput, "cutoff_hours")),
    )


@st.composite
def plan_cases(draw: st.DrawFn) -> PlanCase:
    label = draw(st.sampled_from(COURSE_KINDS))
    athlete = draw(athletes())
    packed = draw(
        st.lists(packed_items(), max_size=_max_len(GeneratePlanRequest, "packed_vest_items"))
    )
    stations = draw(
        st.lists(
            station_draws(),
            max_size=_max_len(GeneratePlanRequest, "aid_stations"),
            unique_by=lambda station: station.name,
        )
    )
    if label in BUNDLED_IDS:
        taken = {station.name for station in bundled_races()[label].aid_stations}
        assume(all(station.name not in taken for station in stations))
        return PlanCase(label=label, athlete=athlete, stations=stations, packed=packed)

    distance_km = draw(
        st.floats(
            min_value=0.5,
            max_value=_max_fit_km(label),
            exclude_max=label == "all-climb",
            allow_nan=False,
            allow_infinity=False,
        )
    )
    # 0.5 km is inside Race.distance_km (0, 500]. Shorter tracks collapse under float lat.
    # Leave LAT_WALK_MARGIN_DEG under the pole so the northward walk stays <= 90.
    dlat = distance_km * 1000.0 / METERS_PER_DEG_LAT
    lat_low, lat_high, _, _ = _bounds(TrackPoint, "lat")
    assert lat_high is not None
    lat = draw(
        st.floats(
            min_value=lat_low,
            max_value=lat_high - dlat - LAT_WALK_MARGIN_DEG,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    lon = draw(_floats(TrackPoint, "lon"))
    elevation = draw(_floats(TrackPoint, "elevation_m"))
    assume(_path_stays_inside(label, distance_km, elevation))
    return PlanCase(
        label=label,
        athlete=athlete,
        stations=stations,
        packed=packed,
        distance_km=distance_km,
        origin_lat=lat,
        origin_lon=lon,
        origin_elevation_m=elevation,
    )


@dataclass
class BuiltCourse:
    label: str
    profile: list[TrackPoint]
    stations: list[AidStation]
    athlete: AthleteInput
    packed: list[PackedVestItem]
    declared_distance_km: float


def build_course(case: PlanCase) -> BuiltCourse:
    if case.label in BUNDLED_IDS:
        race = bundled_races()[case.label]
        profile = list(race.profile)
        declared = race.distance_km
    else:
        assert case.distance_km is not None
        profile = build_profile(
            case.label,
            case.distance_km,
            case.origin_lat,
            case.origin_lon,
            case.origin_elevation_m,
        )
        declared = prepare_course(profile)[-1].distance_m / 1000.0
    course = prepare_course(profile)
    stations: list[AidStation] = []
    if case.label in BUNDLED_IDS:
        stations.extend(bundled_races()[case.label].aid_stations)
    total_m = course[-1].distance_m
    for draw_station in case.stations:
        target_m = draw_station.distance_km * 1000.0
        point = location_at_distance(course, min(target_m, total_m))
        stations.append(
            AidStation(
                name=draw_station.name,
                distance_km=draw_station.distance_km,
                lat=point.lat,
                lon=point.lon,
                elevation_m=point.elevation_m,
                has_water=draw_station.has_water,
                has_food=draw_station.has_food,
                cutoff_hours=draw_station.cutoff_hours,
            )
        )
    return BuiltCourse(
        label=case.label,
        profile=profile,
        stations=stations,
        athlete=case.athlete,
        packed=list(case.packed),
        declared_distance_km=declared,
    )


def _generate(
    built: BuiltCourse,
    *,
    athlete: AthleteInput | None = None,
    declared_distance_km: float | None = None,
    pace_ratio: float = 1.0,
    rescue_start_min: float | None = None,
) -> RacePlan:
    runner = built.athlete if athlete is None else athlete
    return generate_plan(
        race_id=built.label,
        race_name=built.label,
        declared_distance_km=built.declared_distance_km if declared_distance_km is None else declared_distance_km,
        aid_stations=built.stations,
        profile=built.profile,
        athlete=runner,
        pace_ratio=pace_ratio,
        rescue_start_min=rescue_start_min,
        packed_vest_items=built.packed or None,
    )


def _window_carbs(plan: RacePlan) -> float:
    return plan.athlete.carbs_g_per_hour * plan.athlete.feed_interval_min / 60.0


def _minute_at(plan: RacePlan, distance_m: float) -> float:
    if distance_m <= plan.segments[0].start_distance_m:
        return plan.segments[0].start_minute
    for segment in plan.segments:
        if segment.start_distance_m <= distance_m <= segment.end_distance_m:
            span = segment.end_distance_m - segment.start_distance_m
            fraction = 0.0 if span <= 0 else (distance_m - segment.start_distance_m) / span
            return segment.start_minute + fraction * segment.predicted_minutes
    return plan.segments[-1].end_minute


def _approach_min(plan: RacePlan, distance_km: float) -> float:
    previous_km = plan.segments[0].start_distance_m / 1000.0
    for station in sorted(plan.aid_stations, key=lambda item: item.distance_km):
        if station.distance_km >= distance_km - 0.001:
            break
        if station.distance_km > previous_km:
            previous_km = station.distance_km
    return _minute_at(plan, distance_km * 1000.0) - _minute_at(plan, previous_km * 1000.0)


def _safer_ground(plan: RacePlan, ideal_minute: float) -> bool:
    window_lo = ideal_minute - WINDOW_MIN
    window_hi = ideal_minute + WINDOW_MIN
    for segment in plan.segments:
        overlap_lo = max(segment.start_minute, window_lo)
        overlap_hi = min(segment.end_minute, window_hi)
        if overlap_lo <= overlap_hi and segment.zone is not DigestiveZone.red:
            return True
    return False


def _window_overlaps(plan: RacePlan, ideal_minute: float) -> bool:
    window_lo = ideal_minute - WINDOW_MIN
    window_hi = ideal_minute + WINDOW_MIN
    for segment in plan.segments:
        if max(segment.start_minute, window_lo) <= min(segment.end_minute, window_hi):
            return True
    return False


def _floored(plan: RacePlan, pace: float, ratio: float) -> bool:
    for segment in plan.segments:
        raw = (segment.distance_m / 1000.0) * pace * segment.cost_factor * ratio
        if raw < 0.01:
            return True
    return False


def _assert_finite(value: object, path: str) -> None:
    if isinstance(value, float):
        assert math.isfinite(value), path
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_finite(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite(item, f"{path}[{index}]")


def _assert_segments(plan: RacePlan, athlete: AthleteInput) -> None:
    segments = plan.segments
    assert segments
    assert segments[0].start_distance_m == pytest.approx(0.0, abs=1e-3)
    assert segments[0].start_minute == pytest.approx(0.0, abs=1e-6)
    assert plan.clock_start_min == pytest.approx(0.0, abs=1e-6)
    total_m = segments[-1].end_distance_m
    assert plan.distance_km == pytest.approx(total_m / 1000.0, abs=1e-3)
    assert sum(segment.distance_m for segment in segments) == pytest.approx(total_m, abs=1.0)
    assert plan.predicted_duration_min == pytest.approx(
        segments[-1].end_minute - segments[0].start_minute,
        abs=1e-4,
    )
    assert sum(segment.predicted_minutes for segment in segments) == pytest.approx(
        plan.predicted_duration_min,
        abs=1e-3,
    )
    assert plan.elevation_gain_m == pytest.approx(sum(segment.elevation_gain_m for segment in segments))
    assert sum(plan.zone_counts.values()) == len(segments)
    edges = zone_edges_for(athlete)
    for previous, segment in zip(segments, segments[1:]):
        assert previous.end_distance_m == pytest.approx(segment.start_distance_m, abs=1e-3)
        assert previous.end_minute == pytest.approx(segment.start_minute, abs=1e-4)
        assert previous.index + 1 == segment.index
    for segment in segments:
        assert segment.distance_m == pytest.approx(segment.end_distance_m - segment.start_distance_m, abs=1e-3)
        assert segment.predicted_minutes == pytest.approx(segment.end_minute - segment.start_minute, abs=1e-4)
        assert segment.zone is classify_zone(segment.grade_percent, *edges)
        assert segment.cost_factor == pytest.approx(gap_cost_factor(segment.grade_percent), rel=1e-9, abs=1e-9)
        assert segment.predicted_pace_min_per_km == pytest.approx(
            segment.predicted_minutes / (segment.distance_m / 1000.0),
            rel=1e-6,
            abs=1e-4,
        )
        raw_minutes = (segment.distance_m / 1000.0) * athlete.flat_pace_min_per_km * segment.cost_factor
        if raw_minutes >= 0.01:
            assert segment.gap_min_per_km == pytest.approx(athlete.flat_pace_min_per_km, rel=1e-6, abs=1e-4)
        assert segment.cost_factor > 0


def _assert_shape(plan: RacePlan, label: str) -> None:
    if label == "flat":
        assert all(segment.zone is DigestiveZone.green for segment in plan.segments)
        assert all(segment.cost_factor == pytest.approx(1.0, abs=1e-6) for segment in plan.segments)
    elif label == "all-climb":
        # The 5-point elevation smooth pulls the ends of a constant climb
        # toward their neighbours. Under about 2 km that can erase the climb,
        # so gain and a red middle slice are only required on a longer course.
        if plan.distance_km >= 2:
            assert plan.elevation_gain_m > 0
            middle = plan.segments[len(plan.segments) // 2]
            assert middle.zone is DigestiveZone.red
            assert middle.cost_factor > 1.0
    elif label == "sawtooth" and plan.distance_km >= 2.0:
        grades = [segment.grade_percent for segment in plan.segments]
        assert any(grade > 0 for grade in grades)
        assert any(grade < 0 for grade in grades)


def _assert_stops(plan: RacePlan, athlete: AthleteInput, total_m: float) -> None:
    short = _is_short_race(athlete)
    stops = plan.fuel_stops
    assert [stop.sequence for stop in stops] == list(range(1, len(stops) + 1))
    finish = plan.clock_start_min + plan.predicted_duration_min
    for earlier, later in zip(stops, stops[1:]):
        assert earlier.scheduled_minute <= later.scheduled_minute + 1e-6
        # Short-race sips sort ahead of a bite at the same minute by carb count.
        # Packed-vest inserts re-sort by the old sequence. Both are outside the
        # aid-merge distance tie-break.
        if not short and not any(stop.reason.startswith("Packed vest") for stop in stops):
            if abs(earlier.scheduled_minute - later.scheduled_minute) <= 1e-6:
                assert earlier.distance_km <= later.distance_km + 1e-6
    by_index = {segment.index: segment for segment in plan.segments}
    for stop in stops:
        assert 0.0 <= stop.distance_km * 1000.0 <= total_m + 1.0
        assert plan.clock_start_min - 1e-3 <= stop.scheduled_minute <= finish + 1e-3
        segment = by_index[stop.segment_index]
        assert segment.start_distance_m - 1.0 <= stop.distance_km * 1000.0 <= segment.end_distance_m + 1.0
        assert segment.start_minute - 1e-3 <= stop.scheduled_minute <= segment.end_minute + 1e-3
        # A short-race fallback may leave the +/- 6 min window only when nothing overlaps it.
        if abs(stop.shift_minutes) > WINDOW_MIN + 1e-6:
            assert not _window_overlaps(plan, stop.ideal_minute)
        if stop.zone is DigestiveZone.red and not stop.at_aid_station:
            # Rescue targets and hydration sips do not prefer green ground.
            # timing._place_feed documents that a sip ignores zone.
            if "GI Rescue" in stop.reason or stop.form is FuelForm.rescue_sip:
                continue
            if short and stop.carbs_g == 0:
                continue
            assert not _safer_ground(plan, stop.ideal_minute)


def _assert_doses(plan: RacePlan, athlete: AthleteInput) -> None:
    short = _is_short_race(athlete)
    window = _window_carbs(plan)
    # A yellow trail feed may be a half dose of gel or drink mix. A solid, or
    # more than half the window, is a failure. self_check asserts the same rule.
    #
    # Known-acceptable: delivered g/h is not capped at the hourly target. Red
    # forfeits, the quiet window, and descent lock all drop the rate. No ceiling
    # is asserted here.
    #
    # Known-acceptable: a custom vest portion replaces the window grams. The
    # one-dose cap below is the scheduler dose before that replacement.
    if short:
        carb_stops = [stop for stop in plan.fuel_stops if stop.carbs_g > 0]
        assert len(carb_stops) <= 1
        if carb_stops:
            assert carb_stops[0].carbs_g <= 30.0 + 0.05
        assert SHORT_RACE_NOTE in plan.warnings
        assert plan.vest_inventory_summary == ""
        assert not any(stop.at_aid_station for stop in plan.fuel_stops)
        return

    gels = any(key in athlete.vest_foods for key in ("gel_25", "iso_gel_22", "high_carb_gel", "caffeine_gel"))
    switch = EARLY_SAVOURY_MIN if athlete.early_savoury else SAVOURY_START_MIN
    solid_times: list[float] = []
    for stop in plan.fuel_stops:
        stamped = stop.reason.startswith("Packed vest")
        if stop.zone is DigestiveZone.red and not stop.at_aid_station:
            # A packed piece on red is still a micro-dose. It does not become a full gel.
            assert stop.form in {FuelForm.micro_dose, FuelForm.sip, FuelForm.rescue_sip}
            if stop.form is FuelForm.micro_dose:
                assert stop.carbs_g <= RED_MICRO_DOSE_G + 1e-6
                assert stop.carbs_g <= window + 0.05
        if stop.at_aid_station or stamped or athlete.custom_vest_items:
            pass
        else:
            assert stop.carbs_g <= window * 1.5 + 0.05
            assert stop.carbs_g <= window + 0.05
            if stop.zone is DigestiveZone.yellow:
                assert stop.carbs_g <= window * 0.5 + 0.05
        if gels and not stamped and stop.zone is DigestiveZone.yellow and not stop.at_aid_station:
            if stop.carbs_g == 0:
                assert stop.form is FuelForm.sip
            else:
                assert stop.form in {FuelForm.gel, FuelForm.liquid}
        if (
            not stop.at_aid_station
            and stop.form is FuelForm.real_food
            and stop.carbs_g > 0
            and (gels or stamped)
        ):
            # Packed solids follow the same 90 minute gap and savoury clock as the menu.
            solid_times.append(stop.scheduled_minute)
            if stop.zone is DigestiveZone.green and stop.scheduled_minute < switch - 1e-6:
                assert stop.flavor != "Savoury/Salty"
    for earlier, later in zip(solid_times, solid_times[1:]):
        assert later - earlier >= SOLID_EVERY_MIN - 0.05

    limit = plan.clock_start_min + PRE_RACE_QUIET_MIN
    for stop in plan.fuel_stops:
        if stop.at_aid_station or stop.form is FuelForm.rescue_sip:
            continue
        if stop.scheduled_minute < limit - 1e-9:
            assert stop.carbs_g == 0.0
            assert stop.form is FuelForm.sip


def _assert_caffeine_and_sweat(plan: RacePlan, athlete: AthleteInput) -> None:
    cap = plan.athlete.weight_kg * CAFFEINE_MG_PER_KG
    assert plan.totals.caffeine_cap_mg == pytest.approx(cap)
    assert plan.totals.caffeine_mg == pytest.approx(sum(stop.caffeine_mg for stop in plan.fuel_stops))
    assert plan.totals.caffeine_mg <= cap + 1e-4
    for stop in plan.fuel_stops:
        if stop.caffeine_mg > 0:
            assert stop.form is FuelForm.gel
    ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    assert plan.athlete.fluid_ml_per_hour == pytest.approx(ml_per_hour)
    assert plan.athlete.sodium_mg_per_hour == pytest.approx(sodium_per_hour)
    expected_carbs = athlete.carbs_g_per_hour * min(1.25, max(0.75, athlete.carb_multiplier))
    assert plan.athlete.carbs_g_per_hour == pytest.approx(expected_carbs)
    assert plan.totals.carbs_g == pytest.approx(sum(stop.carbs_g for stop in plan.fuel_stops))
    assert plan.totals.fluid_ml == pytest.approx(sum(stop.fluid_ml for stop in plan.fuel_stops))
    assert plan.totals.sodium_mg == pytest.approx(sum(stop.sodium_mg for stop in plan.fuel_stops))
    assert (
        plan.totals.stops_on_time + plan.totals.stops_shifted + plan.totals.stops_deferred
        == len(plan.fuel_stops)
    )


def _assert_legs_and_stations(
    plan: RacePlan,
    athlete: AthleteInput,
    requested: list[AidStation],
    total_m: float,
) -> None:
    if not _is_short_race(athlete):
        ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
        for leg in plan.aid_legs:
            hours = leg.predicted_minutes / 60.0
            assert leg.carbs_g == pytest.approx(hours * plan.athlete.carbs_g_per_hour * PACK_BUFFER, abs=0.05)
            assert leg.fluid_ml == pytest.approx(hours * ml_per_hour * PACK_BUFFER, abs=0.05)
            assert leg.sodium_mg == pytest.approx(hours * sodium_per_hour * PACK_BUFFER, abs=0.05)
            accounted = leg.vest_carbs_g + leg.station_carbs_g + leg.carryover_carbs_g
            assert accounted == pytest.approx(leg.carbs_g, abs=0.5)
        if plan.aid_legs and plan.clock_start_min == 0:
            assert plan.aid_legs[0].from_name == "Start"
            assert plan.aid_legs[0].from_km == pytest.approx(0.0, abs=1e-6)
    else:
        if plan.aid_legs:
            flasks = [item for item in plan.aid_legs[0].pack if "Flask" in item.item]
            assert len(flasks) == 1
            assert flasks[0].quantity == (2 if athlete.temperature_c > 25 else 1)
            assert all(not leg.pack for leg in plan.aid_legs[1:])
            assert all(leg.station_carbs_g == 0 and leg.station_meal == "" for leg in plan.aid_legs)

    distances = [station.distance_km for station in plan.aid_stations]
    assert distances == sorted(distances)
    on_course_names = {
        station.name
        for station in requested
        if station.distance_km * 1000.0 <= total_m + 50
    }
    kept = {station.name for station in plan.aid_stations}
    for station in requested:
        if station.distance_km * 1000.0 <= total_m + 50:
            continue
        if station.name in on_course_names:
            continue
        assert station.name not in kept
        needle = f"Aid station {station.name} is past the end of the track and was ignored."
        assert needle in plan.warnings

    for stop in plan.fuel_stops:
        if not stop.at_aid_station:
            continue
        assert stop.form is FuelForm.real_food
        nearby = [
            station
            for station in plan.aid_stations
            if abs(station.distance_km - stop.distance_km) <= 0.05
        ]
        assert nearby
        assert any(station.has_food for station in nearby)
        assert _approach_min(plan, stop.distance_km) + 0.2 >= AID_LEG_MIN_MINUTES


def assert_plan(plan: RacePlan, built: BuiltCourse) -> None:
    course_m = prepare_course(built.profile)[-1].distance_m
    _assert_segments(plan, built.athlete)
    _assert_shape(plan, built.label)
    _assert_stops(plan, built.athlete, course_m)
    _assert_doses(plan, built.athlete)
    _assert_caffeine_and_sweat(plan, built.athlete)
    _assert_legs_and_stations(plan, built.athlete, built.stations, course_m)
    _assert_shift_has_reason(plan)
    _assert_fuel_budget(plan)


def _assert_fuel_budget(plan: RacePlan) -> None:
    """The budget is the stops, the forfeited list, and the segment zones."""

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


def _assert_shift_has_reason(plan: RacePlan) -> None:
    """A stop off its nominal slot explains the move. Forfeited rows have grams."""

    for stop in plan.fuel_stops:
        if abs(stop.scheduled_minute - stop.ideal_minute) > SHIFT_EPSILON_MIN:
            assert stop.reasons
    assert all(item.carbs_g > 0 for item in plan.forfeited)


def _round_half_up(value: float, digits: int) -> float:
    scale = 10**digits
    return math.floor(value * scale + 0.5) / scale


def _stop_rows(stops: list[FuelStop], minimum_minute: float) -> list[tuple[object, ...]]:
    rows = []
    for stop in stops:
        if stop.scheduled_minute + 1e-6 < minimum_minute:
            continue
        rows.append(
            (
                round(stop.scheduled_minute, 4),
                round(stop.distance_km, 4),
                round(stop.carbs_g, 4),
                stop.form.value,
                stop.zone.value,
            )
        )
    return rows


@FAST
@given(case=plan_cases())
def test_plan_is_finite_json_and_deterministic(case: PlanCase) -> None:
    built = build_course(case)
    plan = _generate(built)
    again = _generate(built)
    _assert_finite(plan.model_dump(mode="python"), "plan")
    payload = plan.model_dump(mode="json")
    assert json.loads(json.dumps(payload, allow_nan=False)) == payload
    assert again.model_dump(mode="json") == payload


@FAST
@given(case=plan_cases())
def test_shifted_stop_has_a_reason(case: PlanCase) -> None:
    """Property: scheduled time off the nominal slot means at least one reason."""

    _assert_shift_has_reason(_generate(build_course(case)))


@FAST
@given(case=plan_cases())
def test_schedule_invariants(case: PlanCase) -> None:
    built = build_course(case)
    assert_plan(_generate(built), built)


@SLOW
@given(case=plan_cases(), data=st.data())
def test_pace_scales_duration_and_weight_does_not(case: PlanCase, data: st.DataObject) -> None:
    built = build_course(case)
    base = _generate(built)
    other_weight = data.draw(_floats(AthleteInput, "weight_kg"))
    weighted = _generate(built, athlete=built.athlete.model_copy(update={"weight_kg": other_weight}))
    assert weighted.predicted_duration_min == pytest.approx(base.predicted_duration_min, rel=1e-9, abs=1e-4)

    fast_pace = data.draw(_floats(AthleteInput, "flat_pace_min_per_km"))
    slow_pace = data.draw(_floats(AthleteInput, "flat_pace_min_per_km"))
    assume(fast_pace < slow_pace)
    fast = _generate(built, athlete=built.athlete.model_copy(update={"flat_pace_min_per_km": fast_pace}))
    slow = _generate(built, athlete=built.athlete.model_copy(update={"flat_pace_min_per_km": slow_pace}))
    # Known-acceptable: a segment shorter than 0.01 min is clamped, so a tiny
    # course can tie. Duration is still never longer at the faster pace.
    assert fast.predicted_duration_min <= slow.predicted_duration_min + 1e-6
    if not _floored(fast, fast_pace, 1.0) and not _floored(slow, slow_pace, 1.0):
        assert fast.predicted_duration_min < slow.predicted_duration_min
        assert fast.predicted_duration_min == pytest.approx(
            slow.predicted_duration_min * fast_pace / slow_pace,
            rel=1e-6,
            abs=1e-3,
        )

    low_ratio = data.draw(st.floats(MIN_PACE_RATIO, MAX_PACE_RATIO, allow_nan=False, allow_infinity=False))
    high_ratio = data.draw(st.floats(MIN_PACE_RATIO, MAX_PACE_RATIO, allow_nan=False, allow_infinity=False))
    assume(low_ratio < high_ratio)
    shorter = _generate(built, pace_ratio=low_ratio)
    longer = _generate(built, pace_ratio=high_ratio)
    assert shorter.predicted_duration_min <= longer.predicted_duration_min + 1e-6
    pace = built.athlete.flat_pace_min_per_km
    if not _floored(shorter, pace, low_ratio) and not _floored(longer, pace, high_ratio):
        assert shorter.predicted_duration_min < longer.predicted_duration_min
        assert shorter.predicted_duration_min == pytest.approx(
            longer.predicted_duration_min * low_ratio / high_ratio,
            rel=1e-6,
            abs=1e-3,
        )


@SLOW
@given(case=plan_cases(), data=st.data())
def test_target_time_lands_within_half_a_minute(case: PlanCase, data: st.DataObject) -> None:
    """Pace = 8 * target / duration at 8 min/km, clamped like Build Plan. One plan uses that pace."""

    built = build_course(case)
    target = data.draw(_floats(AthleteInput, "target_time_minutes"))
    probe_athlete = built.athlete.model_copy(
        update={"flat_pace_min_per_km": 8.0, "target_time_minutes": target}
    )
    probe = _generate(built, athlete=probe_athlete)
    raw = 8.0 * (target / probe.predicted_duration_min)
    # Outside the client clamp a miss is allowed. The engine does not solve finish time itself.
    if not CLIENT_PACE_MIN <= raw <= CLIENT_PACE_MAX:
        return
    pace = _round_half_up(raw, 3)
    solved = _generate(
        built,
        athlete=probe_athlete.model_copy(update={"flat_pace_min_per_km": pace}),
    )
    # Known-acceptable: the 0.01 min slice floor breaks the linear pace scaling.
    if _floored(probe, 8.0, 1.0) or _floored(solved, pace, 1.0):
        return
    assert abs(solved.predicted_duration_min - target) <= 0.5 + 1e-6


@SLOW
@given(case=plan_cases(), data=st.data())
def test_on_pace_recalibrate_keeps_later_stops(case: PlanCase, data: st.DataObject) -> None:
    built = build_course(case)
    plan = _generate(built)
    course = prepare_course(built.profile)
    total_m = course[-1].distance_m
    valid = [
        index
        for index, point in enumerate(course)
        if MIN_PACE_SAMPLE_MIN <= _minute_at(plan, point.distance_m) and point.distance_m < total_m - 100
    ]
    assume(valid)
    point = course[valid[data.draw(st.integers(min_value=0, max_value=len(valid) - 1))]]
    snapped, _off_m, _index = snap_to_track(course, point.lat, point.lon)
    elapsed = _minute_at(plan, snapped.distance_m)
    assume(elapsed >= MIN_PACE_SAMPLE_MIN)
    updated = recalibrate(
        race_id=built.label,
        race_name=built.label,
        declared_distance_km=built.declared_distance_km,
        aid_stations=built.stations,
        profile=built.profile,
        athlete=built.athlete,
        latitude=point.lat,
        longitude=point.lon,
        elapsed_min=elapsed,
        gi_rescue=False,
        packed_vest_items=built.packed or None,
    )
    assert updated.snap.on_course
    assert updated.snap.pace_ratio == pytest.approx(1.0, abs=1e-3)
    assert updated.minutes_behind_plan == pytest.approx(0.0, abs=0.05)
    assert updated.remaining.clock_start_min == pytest.approx(elapsed, abs=0.05)
    for stop in updated.remaining.fuel_stops:
        assert stop.scheduled_minute + 0.05 >= elapsed

    # FINDINGS.md: a packed vest is stamped onto the stops after the schedule,
    # so later pieces are not the original suffix. A short race never reads
    # the packed vest, so its stops are still the original suffix.
    if built.packed and not _is_short_race(built.athlete):
        return
    assert _stop_rows(plan.fuel_stops, elapsed) == _stop_rows(updated.remaining.fuel_stops, 0.0)


@SLOW
@given(case=plan_cases(), data=st.data())
def test_gi_rescue_phases(case: PlanCase, data: st.DataObject) -> None:
    built = build_course(case)
    duration = _generate(built).predicted_duration_min
    assume(duration > RESCUE_COOLDOWN_MIN + built.athlete.feed_interval_min + 5)
    start = data.draw(
        st.floats(
            min_value=0.0,
            max_value=duration - RESCUE_COOLDOWN_MIN,
            allow_nan=False,
            allow_infinity=False,
        )
    )
    plan = _generate(built, rescue_start_min=start)
    window = _window_carbs(plan)
    nil_end = start + RESCUE_NIL_CARB_MIN
    cool_end = start + RESCUE_COOLDOWN_MIN
    half = [stop for stop in plan.fuel_stops if "Half-size while GI Rescue" in stop.reason]
    assert len(half) <= RESCUE_GENTLE_FEEDS
    for stop in half:
        assert stop.ideal_minute + 1e-6 >= cool_end
        # A custom portion can replace the halved window. The code still marks it.
        if not built.athlete.custom_vest_items:
            assert stop.carbs_g <= window * 0.5 + 0.05
    for stop in plan.fuel_stops:
        if stop.at_aid_station or stop.reason.startswith("Packed vest"):
            # FINDINGS.md: a packed stamp replaces the rescue dose.
            continue
        if start - 1e-6 <= stop.ideal_minute < nil_end:
            assert stop.carbs_g == 0.0
            assert stop.form is FuelForm.rescue_sip
        elif nil_end - 1e-6 <= stop.ideal_minute < cool_end:
            assert "GI Rescue" in stop.reason
            if stop.zone is DigestiveZone.red:
                assert stop.carbs_g == 0.0
                assert stop.form is FuelForm.rescue_sip
            else:
                assert stop.form is FuelForm.liquid
                assert stop.carbs_g == pytest.approx(RESCUE_EASE_CARBS_G, abs=0.05)


@SLOW
@given(case=plan_cases(), declared=_floats(Race, "distance_km"))
def test_declared_distance_warning(case: PlanCase, declared: float) -> None:
    built = build_course(case)
    plan = _generate(built, declared_distance_km=declared)
    measured = prepare_course(built.profile)[-1].distance_m / 1000.0
    gap = abs(measured - declared) / declared
    warned = any("differs from the stated" in warning for warning in plan.warnings)
    assert warned is (gap > 0.10)


@pytest.mark.parametrize("kind", COURSE_KINDS)
def test_each_bundled_and_synthetic_course(kind: str) -> None:
    """Every course shape runs once with the default athlete, not only when Hypothesis draws it."""

    athlete = AthleteInput()
    if kind in BUNDLED_IDS:
        race = bundled_races()[kind]
        case = PlanCase(label=kind, athlete=athlete, stations=[], packed=[])
    else:
        case = PlanCase(
            label=kind,
            athlete=athlete,
            stations=[
                StationDraw("Food hut", 8.0, True, True, None),
                StationDraw("Water only", 14.0, True, False, None),
                StationDraw("Past the finish", 450.0, True, True, None),
            ],
            packed=[],
            distance_km=20.0,
            origin_lat=42.6,
            origin_lon=23.3,
            origin_elevation_m=1200.0,
        )
    built = build_course(case)
    assert_plan(_generate(built), built)


def _flat(distance_m: float) -> list[TrackPoint]:
    return build_profile("flat", distance_m / 1000.0, 42.6, 23.3, 1200.0)


def _climb(distance_m: float) -> list[TrackPoint]:
    return build_profile("all-climb", distance_m / 1000.0, 42.6, 23.3, 1200.0)


def test_logged_on_pace_recalibrate_quiet_window() -> None:
    profile = _flat(30_000)
    athlete = AthleteInput()
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=profile,
        athlete=athlete,
    )
    course = prepare_course(profile)
    point = min(course, key=lambda sample: abs(_minute_at(plan, sample.distance_m) - 60.0))
    elapsed = _minute_at(plan, point.distance_m)
    updated = recalibrate(
        race_id="flat",
        race_name="flat",
        declared_distance_km=plan.declared_distance_km,
        aid_stations=[],
        profile=profile,
        athlete=athlete,
        latitude=point.lat,
        longitude=point.lon,
        elapsed_min=elapsed,
        gi_rescue=False,
    )
    original = _stop_rows(plan.fuel_stops, elapsed)
    rebuilt = _stop_rows(updated.remaining.fuel_stops, 0.0)
    assert original == rebuilt


def test_logged_short_race_recalibrate_changes_stops() -> None:
    profile = _flat(20_000)
    athlete = AthleteInput(target_time_minutes=90)
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=profile,
        athlete=athlete,
    )
    course = prepare_course(profile)
    point = min(course, key=lambda sample: abs(_minute_at(plan, sample.distance_m) - 20.0))
    elapsed = _minute_at(plan, point.distance_m)
    updated = recalibrate(
        race_id="flat",
        race_name="flat",
        declared_distance_km=plan.declared_distance_km,
        aid_stations=[],
        profile=profile,
        athlete=athlete,
        latitude=point.lat,
        longitude=point.lon,
        elapsed_min=elapsed,
        gi_rescue=False,
    )
    assert _stop_rows(plan.fuel_stops, elapsed) == _stop_rows(updated.remaining.fuel_stops, 0.0)


def test_logged_packed_vest_on_red_and_caffeine() -> None:
    profile = _climb(8_000)
    athlete = AthleteInput(weight_kg=30.1)
    packed = [
        PackedVestItem(id="caf", name="Caf Gel", carbs_g=40, caffeine_mg=400, qty=1, form="gel"),
    ]
    plan = generate_plan(
        race_id="climb",
        race_name="climb",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=profile,
        athlete=athlete,
        packed_vest_items=packed,
    )
    cap = athlete.weight_kg * CAFFEINE_MG_PER_KG
    red = [
        stop
        for stop in plan.fuel_stops
        if stop.zone is DigestiveZone.red and not stop.at_aid_station and stop.reason.startswith("Packed vest")
    ]
    assert red
    assert all(stop.form is FuelForm.micro_dose and stop.carbs_g <= RED_MICRO_DOSE_G for stop in red)
    assert plan.totals.caffeine_mg <= cap + 1e-6


def _trail_solids(plan: RacePlan) -> list[FuelStop]:
    return [
        stop
        for stop in plan.fuel_stops
        if stop.form is FuelForm.real_food and stop.carbs_g > 0 and not stop.at_aid_station
    ]


def _assert_solid_gap(solids: list[FuelStop]) -> None:
    assert all(
        later.scheduled_minute - earlier.scheduled_minute >= SOLID_EVERY_MIN - 0.05
        for earlier, later in zip(solids, solids[1:])
    )
    assert all(stop.scheduled_minute >= SAVOURY_START_MIN or stop.flavor != "Savoury/Salty" for stop in solids)


def test_logged_packed_solids_ignore_palate_gap() -> None:
    """Packed solids keep the 90 minute gap and wait for the savoury clock.

    Flat 16 km finishes near minute 128. After the quiet window, two trail
    bites cannot sit 90 minutes apart, so the savoury chips stay in the vest
    instead of landing at minute 60 and minute 90. Flat 30 km is long enough
    for two solids, and those two follow the same rules.
    """

    athlete = AthleteInput()
    packed = [
        PackedVestItem(
            id="chips",
            name="Salted Chips",
            carbs_g=20,
            qty=2,
            flavor="savoury",
            form="solid",
        )
    ]
    short = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=_flat(16_000),
        athlete=athlete,
        packed_vest_items=packed,
    )
    short_solids = _trail_solids(short)
    assert short_solids
    assert all(not stop.reason.startswith("Packed vest") for stop in short_solids)
    _assert_solid_gap(short_solids)

    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=_flat(30_000),
        athlete=athlete,
        packed_vest_items=packed,
    )
    solids = _trail_solids(plan)
    assert len(solids) >= 2
    _assert_solid_gap(solids)


def test_logged_gi_rescue_menu_and_half_size() -> None:
    profile = _flat(30_000)
    athlete = AthleteInput()
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=profile,
        athlete=athlete,
        rescue_start_min=140,
    )
    ease = [
        stop
        for stop in plan.fuel_stops
        if 160 <= stop.ideal_minute < 185 and "GI Rescue" in stop.reason
    ]
    assert ease
    assert all(stop.form is FuelForm.liquid and stop.form is not FuelForm.real_food for stop in ease)
    early_half = [stop for stop in plan.fuel_stops if "Half-size while GI Rescue" in stop.reason and stop.scheduled_minute < 140]
    assert not early_half
    after = [
        stop
        for stop in plan.fuel_stops
        # Ease lines start with "GI Rescue:". The half-size sentence also
        # names GI Rescue, so a substring check would drop the feed this asserts.
        if stop.ideal_minute >= 185 and stop.carbs_g > 0 and not stop.reason.startswith("GI Rescue")
    ]
    assert after
    assert "Half-size while GI Rescue" in after[0].reason


def test_logged_aid_leg_plate_and_carryover_exceed_budget() -> None:
    profile = _flat(12_000)
    course = prepare_course(profile)
    stations = []
    for name, km in (("Food A", 6.0), ("Food B", 11.0)):
        point = location_at_distance(course, km * 1000.0)
        stations.append(
            AidStation(
                name=name,
                distance_km=point.distance_m / 1000.0,
                lat=point.lat,
                lon=point.lon,
                elevation_m=point.elevation_m,
                has_food=True,
            )
        )
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=stations,
        profile=profile,
        athlete=AthleteInput(),
    )
    leg = next(item for item in plan.aid_legs if item.from_name == "Food A")
    accounted = leg.vest_carbs_g + leg.station_carbs_g + leg.carryover_carbs_g
    assert accounted == pytest.approx(leg.carbs_g, abs=0.5)


def test_logged_quiet_window_erases_rescue_ease() -> None:
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=_flat(20_000),
        athlete=AthleteInput(),
        rescue_start_min=0,
    )
    ease = [stop for stop in plan.fuel_stops if 20 <= stop.ideal_minute < 45]
    assert ease
    assert all(
        stop.form is FuelForm.liquid and stop.carbs_g == pytest.approx(RESCUE_EASE_CARBS_G, abs=0.05)
        for stop in ease
    )


def test_logged_descent_lock_erases_rescue_ease() -> None:
    """20 km at a constant -10% (yellow). Rescue starts at 35 min, so 55–75 min is the ease phase."""

    lat, lon, elevation = 42.6, 23.3, 3500.0
    distance_m = 20_000.0
    grade = -10.0
    points = [TrackPoint(lat=lat, lon=lon, elevation_m=elevation)]
    steps = int(distance_m / STEP_M)
    step_len = distance_m / steps
    rise = (grade / 100.0) * step_len
    dlat = step_len / METERS_PER_DEG_LAT
    for _ in range(steps):
        lat += dlat
        elevation += rise
        points.append(TrackPoint(lat=lat, lon=lon, elevation_m=elevation))
    plan = generate_plan(
        race_id="descent",
        race_name="descent",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=points,
        athlete=AthleteInput(),
        rescue_start_min=35,
    )
    ease = [stop for stop in plan.fuel_stops if 55 <= stop.ideal_minute < 80]
    assert ease
    assert all(stop.zone is DigestiveZone.yellow for stop in ease)
    assert all(
        stop.form is FuelForm.liquid and stop.carbs_g == pytest.approx(RESCUE_EASE_CARBS_G, abs=0.05)
        for stop in ease
    )


def test_logged_aid_plate_erases_rescue_ease() -> None:
    """Flat 16 km, food at 8 km (minute 64). Rescue starts at 40, so minute 60 is an ease feed."""

    profile = _flat(16_000)
    course = prepare_course(profile)
    point = location_at_distance(course, 8_000)
    station = AidStation(
        name="Table",
        distance_km=point.distance_m / 1000.0,
        lat=point.lat,
        lon=point.lon,
        elevation_m=point.elevation_m,
        has_food=True,
    )
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[station],
        profile=profile,
        athlete=AthleteInput(),
        rescue_start_min=40,
    )
    ease = next(stop for stop in plan.fuel_stops if stop.ideal_minute == pytest.approx(60, abs=0.05))
    assert ease.at_aid_station is False
    assert ease.zone is DigestiveZone.green
    assert ease.form is FuelForm.liquid
    assert ease.carbs_g == pytest.approx(RESCUE_EASE_CARBS_G, abs=0.05)


def test_logged_tiny_custom_portion_overflows() -> None:
    athlete = AthleteInput(
        carbs_g_per_hour=2,
        carb_multiplier=1.25,
        vest_foods=[],
        custom_vest_items=[
            CustomFoodItem(key="", name="a", carbs_g=2.2250738585072014e-308, flavor="Sweet"),
        ],
    )
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=_flat(17_000),
        athlete=athlete,
    )
    assert plan.segments


def test_logged_short_race_ignores_gi_rescue() -> None:
    profile = _flat(12_000)
    plan = generate_plan(
        race_id="flat",
        race_name="flat",
        declared_distance_km=0.0,
        aid_stations=[],
        profile=profile,
        athlete=AthleteInput(target_time_minutes=90),
        rescue_start_min=20,
    )
    assert any(stop.form is FuelForm.rescue_sip for stop in plan.fuel_stops)
