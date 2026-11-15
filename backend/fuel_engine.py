"""Grade Adjusted Pace, digestive zones, and the fueling schedule.

The product spec names these behaviors. This module is the concrete version:

- GAP uses the Minetti 2002 running-cost polynomial. The athlete's flat
  effort pace is multiplied by C(grade) / C(0) to get time on that slope.
- Digestive zones: green from -8% to +5%, yellow up to +10% or down to -12%,
  and red outside that. A feed may move up to 6 minutes to reach green, then
  yellow. If the whole window is red, that window takes a 15 g liquid
  micro-dose and the rest of its carbs are forfeited. Windows do not share
  a carb debt.
- Aid-station packs, including Start at 0 km, are
  leg hours x hourly target x 1.15.
- Fluid and sodium come from air temperature and body weight.
- Live GPS snaps to the nearest point on the track. Remaining splits are
  stretched by actual_time / predicted_time.
- GI Rescue pauses calories, then eases back with half doses.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.gpx_parser import (
    CoursePoint,
    CourseSegment,
    attach_track,
    haversine_m,
    location_at_distance,
    parse_gpx_xml,
    prepare_course,
    slice_segments,
    smooth_elevation,
)
from backend.models import (
    AidLeg,
    AidStation,
    AthleteInput,
    CustomAidInput,
    DigestiveZone,
    FuelForm,
    FuelStop,
    LockerItem,
    PackItem,
    PlanTotals,
    Race,
    RacePlan,
    RecalibrateResponse,
    Segment,
    SnapResult,
    TrackPoint,
)

# Minetti et al., J Appl Physiol, 2002. Gradient is rise/run, not percent.
# C(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6  (J/kg/m)
FLAT_COST_J_PER_KG_M = 3.6
MIN_COST_J_PER_KG_M = 1.0
MAX_ABS_GRADE_FRACTION = 0.40

# Green is closed on both ends. Yellow uphill starts just above +5% and
# includes +10%. Yellow downhill starts just below -8% and includes -12%.
GREEN_MIN_GRADE = -8.0
GREEN_MAX_GRADE = 5.0
YELLOW_UPHILL_MAX = 10.0
YELLOW_DOWNHILL_MIN = -12.0
WINDOW_MIN = 6.0
PACK_BUFFER = 1.15
GEL_CARBS_G = 25.0
FLASK_ML = 500.0
CAPSULE_SODIUM_MG = 200.0
# Steep ground cannot take a full gel. 15 g is the most this window will feed.
RED_MICRO_DOSE_G = 15.0
# A caffeinated gel is placed before a steep climb, or once the clock is
# past halfway, and the running total stops at 5 mg per kg of body weight.
CAFFEINE_MG_PER_KG = 5.0
CLIMB_LOOKAHEAD_MIN = 20.0
MAJOR_CLIMB_GRADE = 8.0
# After 3.5 hours, green feeds in the second half leave the sweet gels behind.
PALATE_FATIGUE_MIN = 210.0
AID_FOOD_MIN_MINUTE = 75.0
AID_BITE_CARBS_G = 28.0
FLAT_GREEN_GRADE = 3.0
AID_PLATE = "Aid Station Plate: 2 pieces banana / salted potatoes / orange wedges + broth/cola"

# GI Rescue: 20 min with no calories, then small liquid carbs until 45 min,
# then two half-size feeds. Each of those feeds stands alone.
RESCUE_NIL_CARB_MIN = 20.0
RESCUE_COOLDOWN_MIN = 45.0
RESCUE_SIP_INTERVAL_MIN = 10.0
RESCUE_SIP_FLUID_ML = 80.0
RESCUE_SIP_SODIUM_MG = 150.0
RESCUE_EASE_CARBS_G = 12.0
RESCUE_EASE_FLUID_ML = 120.0
RESCUE_EASE_SODIUM_MG = 200.0
RESCUE_GENTLE_FEEDS = 2

# Below a few minutes of evidence, a GPS fix near the start must not rewrite the race.
MIN_PACE_SAMPLE_MIN = 5.0
MIN_PACE_RATIO = 0.6
MAX_PACE_RATIO = 1.8
OFF_COURSE_M = 75.0
GAP_MODEL_NAME = "minetti_2002"

METERS_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class _Target:
    minute: float
    kind: str  # "normal", "rescue_nil", or "rescue_ease"


@dataclass
class _Dose:
    carbs_g: float
    fluid_ml: float
    sodium_mg: float
    form: FuelForm
    gentle_left: int
    reason: str


def metabolic_cost_j_per_kg_m(grade_fraction: float) -> float:
    """Energy cost of running one metre at this gradient, from Minetti 2002."""

    grade = min(MAX_ABS_GRADE_FRACTION, max(-MAX_ABS_GRADE_FRACTION, grade_fraction))
    cost = (
        155.4 * grade**5
        - 30.4 * grade**4
        - 43.3 * grade**3
        + 46.3 * grade**2
        + 19.5 * grade
        + FLAT_COST_J_PER_KG_M
    )
    return max(cost, MIN_COST_J_PER_KG_M)


def gap_cost_factor(grade_percent: float) -> float:
    """How many times longer this slope takes versus flat ground, at the same effort.

    factor = C(grade) / C(0). Uphill is above 1, mild downhill is below 1.
    """

    return metabolic_cost_j_per_kg_m(grade_percent / 100.0) / FLAT_COST_J_PER_KG_M


def classify_zone(grade_percent: float) -> DigestiveZone:
    """Map net segment grade to a digestive safety zone.

    Green:  -8% <= grade <= +5%. A gel is reasonable here.
    Yellow: +5% < grade <= +10%, or -12% <= grade < -8%. Liquids and a half dose.
    Red:    grade > +10% or grade < -12%. No full gel. A 15 g micro-dose at most.
    """

    if GREEN_MIN_GRADE <= grade_percent <= GREEN_MAX_GRADE:
        return DigestiveZone.green
    uphill_yellow = GREEN_MAX_GRADE < grade_percent <= YELLOW_UPHILL_MAX
    downhill_yellow = YELLOW_DOWNHILL_MIN <= grade_percent < GREEN_MIN_GRADE
    if uphill_yellow or downhill_yellow:
        return DigestiveZone.yellow
    return DigestiveZone.red


def sweat_rates(temperature_c: float, weight_kg: float) -> tuple[float, float]:
    """Hourly fluid (ml) and sodium (mg) from air temperature and body weight.

    Cool (<15°C):       450 + 3 * (kg - 65) ml/h, 500 mg sodium per litre.
    Moderate (15–25°C): 650 + 4 * (kg - 65) ml/h, 700 mg/L.
    Hot (>25°C):        850 + 5 * (kg - 65) ml/h, 950 mg/L.
    """

    mass_delta = weight_kg - 65.0
    if temperature_c < 15.0:
        ml_per_hour = 450.0 + 3.0 * mass_delta
        mg_per_liter = 500.0
    elif temperature_c <= 25.0:
        ml_per_hour = 650.0 + 4.0 * mass_delta
        mg_per_liter = 700.0
    else:
        ml_per_hour = 850.0 + 5.0 * mass_delta
        mg_per_liter = 950.0
    return ml_per_hour, mg_per_liter * ml_per_hour / 1000.0


def _with_sweat_rates(athlete: AthleteInput) -> AthleteInput:
    """Copy the athlete with fluid and sodium set by the temperature formula."""

    ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    return athlete.model_copy(
        update={
            "fluid_ml_per_hour": ml_per_hour,
            "sodium_mg_per_hour": sodium_per_hour,
        }
    )


def build_timed_segments(
    course_segments: list[CourseSegment],
    athlete: AthleteInput,
    pace_ratio: float = 1.0,
) -> list[Segment]:
    """Attach zone, GAP cost, and a race-clock time to every slice.

    predicted_minutes = kilometres * flat_pace * cost_factor * pace_ratio.
    `gap_min_per_km` is the flat pace that matches this effort. On a climb the
    watch pace (`predicted_pace_min_per_km`) is slower than that GAP.
    """

    if pace_ratio <= 0:
        raise ValueError("pace_ratio must be positive")

    clock_min = 0.0
    timed: list[Segment] = []
    for segment in course_segments:
        factor = gap_cost_factor(segment.grade_percent)
        distance_km = segment.distance_m / 1000.0
        minutes = distance_km * athlete.flat_pace_min_per_km * factor * pace_ratio
        minutes = max(minutes, 0.01)
        start_minute = clock_min
        clock_min += minutes
        predicted_pace = minutes / distance_km
        timed.append(
            Segment(
                index=segment.index,
                start_distance_m=segment.start_distance_m,
                end_distance_m=segment.end_distance_m,
                distance_m=segment.distance_m,
                start_lat=segment.start_lat,
                start_lon=segment.start_lon,
                end_lat=segment.end_lat,
                end_lon=segment.end_lon,
                elevation_start_m=segment.elevation_start_m,
                elevation_end_m=segment.elevation_end_m,
                elevation_gain_m=segment.elevation_gain_m,
                elevation_loss_m=segment.elevation_loss_m,
                grade_percent=segment.grade_percent,
                zone=classify_zone(segment.grade_percent),
                cost_factor=factor,
                predicted_minutes=minutes,
                start_minute=start_minute,
                end_minute=clock_min,
                predicted_pace_min_per_km=predicted_pace,
                gap_min_per_km=predicted_pace / factor,
            )
        )
    return timed


def generate_plan(
    *,
    race_id: str,
    race_name: str,
    declared_distance_km: float,
    aid_stations: list[AidStation],
    profile: list[TrackPoint],
    athlete: AthleteInput,
    pace_ratio: float = 1.0,
    not_before_min: float = 0.0,
    from_distance_m: float = 0.0,
    rescue_start_min: float | None = None,
) -> RacePlan:
    """Build a fueling plan for a full course, or for the part still ahead."""

    course = prepare_course(profile)
    computed_km = course[-1].distance_m / 1000.0
    if declared_distance_km <= 0:
        declared_distance_km = computed_km
    timed = build_timed_segments(slice_segments(course), athlete, pace_ratio)
    if from_distance_m > 0:
        timed = _truncate_front(timed, course, from_distance_m)
    if not timed:
        raise ValueError("No course remaining from this point")

    athlete = _with_sweat_rates(athlete)
    stops = schedule_fuel(
        timed,
        course,
        athlete,
        not_before_min=not_before_min,
        rescue_start_min=rescue_start_min,
    )
    warnings = _distance_warnings(declared_distance_km, course[-1].distance_m / 1000.0)

    total_m = course[-1].distance_m
    stations = _stations_on_course(aid_stations, total_m, warnings)
    stops = _insert_aid_bites(stops, stations, timed)
    legs = build_aid_legs(
        stations,
        timed,
        athlete,
        from_distance_m=max(0.0, from_distance_m),
        total_m=total_m,
        stops=stops,
    )
    gain_m = sum(segment.elevation_gain_m for segment in timed)
    clock_start = timed[0].start_minute
    duration = timed[-1].end_minute - clock_start
    return RacePlan(
        race_id=race_id,
        race_name=race_name,
        athlete=athlete,
        gap_model=GAP_MODEL_NAME,
        window_min=int(WINDOW_MIN),
        declared_distance_km=declared_distance_km,
        distance_km=(total_m - max(from_distance_m, 0.0)) / 1000.0,
        elevation_gain_m=gain_m,
        clock_start_min=clock_start,
        predicted_duration_min=duration,
        zone_counts=_zone_counts(timed),
        segments=timed,
        fuel_stops=stops,
        aid_legs=legs,
        aid_stations=stations,
        totals=_totals(stops, athlete),
        warnings=warnings,
    )


def generate_plan_for_race(race: Race, athlete: AthleteInput) -> RacePlan:
    """Build a plan from a race record in `races.json`."""

    return generate_plan(
        race_id=race.id,
        race_name=race.name,
        declared_distance_km=race.distance_km,
        aid_stations=race.aid_stations,
        profile=race.profile,
        athlete=athlete,
    )


def schedule_fuel(
    segments: list[Segment],
    course: list[CoursePoint],
    athlete: AthleteInput,
    not_before_min: float = 0.0,
    rescue_start_min: float | None = None,
) -> list[FuelStop]:
    """Place each feed on the safest segment inside a +/- 6 minute window.

    Every window is planned on its own. A red window does not add carbs to
    the next green one.
    """

    if not segments:
        return []

    finish_min = segments[-1].end_minute
    targets = _feed_targets(
        finish_min=finish_min,
        interval_min=athlete.feed_interval_min,
        not_before_min=not_before_min,
        rescue_start_min=rescue_start_min,
    )
    gentle_left = RESCUE_GENTLE_FEEDS if rescue_start_min is not None else 0
    stops: list[FuelStop] = []
    caffeine_used = 0.0
    race_start = segments[0].start_minute

    for target in targets:
        placed = _place_feed(segments, target.minute, prefer_safe=target.kind == "normal")
        if placed is None:
            continue
        segment, when = placed
        dose = _dose_for_target(
            target=target,
            zone=segment.zone,
            grade_percent=segment.grade_percent,
            shift_min=when - target.minute,
            athlete=athlete,
            gentle_left=gentle_left,
        )
        gentle_left = dose.gentle_left
        product, caffeine_mg, reason = _apply_locker(
            dose,
            segments,
            when,
            race_start,
            finish_min,
            athlete,
            caffeine_used,
        )
        caffeine_used += caffeine_mg
        # A caffeinated gel stays a gel. Every other green feed can rotate onto real food.
        if caffeine_mg > 0:
            flavor = "Sweet"
        else:
            food_name, flavor = _apply_food_preference(
                dose,
                grade_percent=segment.grade_percent,
                zone=segment.zone,
                when=when,
                finish_min=finish_min,
                athlete=athlete,
            )
            reason = dose.reason
            if food_name:
                product = food_name
        distance_m = _distance_at_minute(segment, when)
        location = location_at_distance(course, distance_m)
        stops.append(
            FuelStop(
                sequence=len(stops) + 1,
                ideal_minute=target.minute,
                scheduled_minute=when,
                shift_minutes=when - target.minute,
                distance_km=distance_m / 1000.0,
                latitude=location.lat,
                longitude=location.lon,
                segment_index=segment.index,
                grade_percent=segment.grade_percent,
                zone=segment.zone,
                carbs_g=dose.carbs_g,
                fluid_ml=dose.fluid_ml,
                sodium_mg=dose.sodium_mg,
                form=dose.form,
                reason=reason,
                caffeine_mg=caffeine_mg,
                product=product or food_name,
                flavor=flavor,
            )
        )
    return stops


def build_aid_legs(
    stations: list[AidStation],
    segments: list[Segment],
    athlete: AthleteInput,
    from_distance_m: float,
    total_m: float,
    stops: list[FuelStop] | None = None,
) -> list[AidLeg]:
    """Pack each leg from its duration, not from the stops that happen to fall in it.

    A full race always starts at 0.0 km. The amount to carry is
    leg_hours * hourly_target * 1.15 for carbs, fluid, and sodium.
    """

    marks: list[tuple[str, float]] = []
    if from_distance_m > 1:
        marks.append(("Current position", from_distance_m))
    else:
        marks.append(("Start", 0.0))
    for station in stations:
        distance_m = station.distance_km * 1000.0
        if marks[-1][1] + 30 < distance_m < total_m - 30:
            marks.append((station.name, distance_m))
    if total_m > marks[-1][1] + 1:
        marks.append(("Finish", total_m))

    fluid_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    legs: list[AidLeg] = []
    for index in range(len(marks) - 1):
        from_name, from_m = marks[index]
        to_name, to_m = marks[index + 1]
        start_min = _minute_at_distance(segments, from_m)
        end_min = _minute_at_distance(segments, to_m)
        minutes = max(0.0, end_min - start_min)
        hours = minutes / 60.0
        carbs_g = hours * athlete.carbs_g_per_hour * PACK_BUFFER
        fluid_ml = hours * fluid_per_hour * PACK_BUFFER
        sodium_mg = hours * sodium_per_hour * PACK_BUFFER
        leg_stops = [
            stop
            for stop in (stops or [])
            if from_m / 1000.0 - 0.05 <= stop.distance_km < to_m / 1000.0
        ]
        arrival = next((station for station in stations if station.name == to_name), None)
        station_carbs = 0.0
        station_meal = ""
        if arrival is not None and arrival.has_food and end_min >= AID_FOOD_MIN_MINUTE:
            station_carbs = min(AID_BITE_CARBS_G, carbs_g)
            station_meal = AID_PLATE
        vest_carbs = max(0.0, carbs_g - station_carbs)
        legs.append(
            AidLeg(
                from_name=from_name,
                to_name=to_name,
                from_km=from_m / 1000.0,
                to_km=to_m / 1000.0,
                distance_km=(to_m - from_m) / 1000.0,
                predicted_minutes=minutes,
                carbs_g=carbs_g,
                fluid_ml=fluid_ml,
                sodium_mg=sodium_mg,
                vest_carbs_g=vest_carbs,
                station_carbs_g=station_carbs,
                station_meal=station_meal,
                pack=_pack_items(
                    vest_carbs,
                    fluid_ml,
                    sodium_mg,
                    athlete.locker,
                    leg_stops,
                    athlete.fuel_preference,
                ),
                note="",
            )
        )
    return legs


def snap_custom_aids(course: list[CoursePoint], stations: list[CustomAidInput]) -> list[AidStation]:
    """Move each typed kilometer onto the nearest GPX trackpoint.

    The trackpoint supplies latitude, longitude, elevation, and the kilometer
    the packing list will actually use.
    """

    if not course:
        raise ValueError("A course needs track points before aid stations can be snapped")
    snapped: list[AidStation] = []
    for station in stations:
        target_m = station.distance_km * 1000.0
        point = min(course, key=lambda sample: abs(sample.distance_m - target_m))
        snapped.append(
            AidStation(
                name=station.name.strip(),
                distance_km=point.distance_m / 1000.0,
                lat=point.lat,
                lon=point.lon,
                elevation_m=point.elevation_m,
                has_water=station.has_water,
                has_food=station.has_food,
                cutoff_hours=station.cutoff_hours,
            )
        )
    return snapped


def snap_to_track(points: list[CoursePoint], latitude: float, longitude: float) -> tuple[CoursePoint, float, int]:
    """Project a GPS fix onto the nearest point of the track.

    Each course step is treated as a straight line in a local metre grid
    (equirectangular), then the closest point on that line is kept. This is
    steadier than snapping only to vertices on a switchback.

    Returns the snapped point, the off-course distance in metres, and the
    index of the course step that won.
    """

    if len(points) < 2:
        raise ValueError("Track needs at least two points")

    origin_lat = points[0].lat
    origin_lon = points[0].lon
    px, py = _to_local_xy(latitude, longitude, origin_lat, origin_lon)
    best_off = float("inf")
    best_point = points[0]
    best_index = 0

    for index in range(len(points) - 1):
        left = points[index]
        right = points[index + 1]
        ax, ay = _to_local_xy(left.lat, left.lon, origin_lat, origin_lon)
        bx, by = _to_local_xy(right.lat, right.lon, origin_lat, origin_lon)
        dx = bx - ax
        dy = by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-9:
            fraction = 0.0
        else:
            fraction = ((px - ax) * dx + (py - ay) * dy) / length_sq
            fraction = min(1.0, max(0.0, fraction))
        sx = ax + fraction * dx
        sy = ay + fraction * dy
        off_m = math.hypot(px - sx, py - sy)
        if off_m < best_off:
            best_off = off_m
            best_index = index
            distance_m = left.distance_m + fraction * (right.distance_m - left.distance_m)
            best_point = location_at_distance(points, distance_m)
    return best_point, best_off, best_index


def recalibrate(
    *,
    race_id: str,
    race_name: str,
    declared_distance_km: float,
    aid_stations: list[AidStation],
    profile: list[TrackPoint],
    athlete: AthleteInput,
    latitude: float,
    longitude: float,
    elapsed_min: float,
    gi_rescue: bool,
) -> RecalibrateResponse:
    """Snap a live fix to the track and rebuild the plan for the remaining course.

    pace_ratio = elapsed / predicted_time_at_the_snap. A ratio above 1 means
    the runner is behind the effort plan, so every remaining split gets longer.
    GI Rescue, when requested, starts at `elapsed_min`.
    """

    course = prepare_course(profile)
    baseline = build_timed_segments(slice_segments(course), athlete, pace_ratio=1.0)
    snapped, off_course_m, step_index = snap_to_track(course, latitude, longitude)
    predicted_min = _minute_at_distance(baseline, snapped.distance_m)
    pace_ratio = _pace_ratio(elapsed_min, predicted_min)
    segment_index = _segment_index_at(baseline, snapped.distance_m)

    remaining = generate_plan(
        race_id=race_id,
        race_name=race_name,
        declared_distance_km=declared_distance_km,
        aid_stations=aid_stations,
        profile=profile,
        athlete=athlete,
        pace_ratio=pace_ratio,
        not_before_min=elapsed_min,
        from_distance_m=snapped.distance_m,
        rescue_start_min=elapsed_min if gi_rescue else None,
    )
    # The truncated clock is on the scaled timeline, so "now" matches elapsed.
    minutes_behind = elapsed_min - predicted_min
    snap = SnapResult(
        latitude=snapped.lat,
        longitude=snapped.lon,
        distance_along_m=snapped.distance_m,
        distance_along_km=snapped.distance_m / 1000.0,
        off_course_m=off_course_m,
        on_course=off_course_m <= OFF_COURSE_M,
        segment_index=segment_index if segment_index >= 0 else step_index,
        predicted_minute=predicted_min,
        elapsed_minute=elapsed_min,
        pace_ratio=pace_ratio,
    )
    return RecalibrateResponse(
        snap=snap,
        minutes_behind_plan=minutes_behind,
        gi_rescue_applied=gi_rescue,
        message=_recalibrate_message(snap, minutes_behind, gi_rescue),
        remaining=remaining,
    )


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


def _pace_ratio(elapsed_min: float, predicted_min: float) -> float:
    if predicted_min < MIN_PACE_SAMPLE_MIN:
        return 1.0
    raw = elapsed_min / predicted_min
    return min(MAX_PACE_RATIO, max(MIN_PACE_RATIO, raw))


def _feed_targets(
    finish_min: float,
    interval_min: int,
    not_before_min: float,
    rescue_start_min: float | None,
) -> list[_Target]:
    """Ideal clock times. Rescue replaces the normal grid during its cooldown."""

    targets: list[_Target] = []
    cooldown_end = None
    if rescue_start_min is not None:
        cooldown_end = rescue_start_min + RESCUE_COOLDOWN_MIN
        sip_at = rescue_start_min
        while sip_at < cooldown_end and sip_at < finish_min - 2:
            if sip_at >= not_before_min - 1e-6:
                kind = "rescue_nil" if sip_at < rescue_start_min + RESCUE_NIL_CARB_MIN else "rescue_ease"
                targets.append(_Target(sip_at, kind))
            sip_at += RESCUE_SIP_INTERVAL_MIN

    ideal = float(interval_min)
    while ideal <= finish_min - 10:
        inside_rescue = cooldown_end is not None and rescue_start_min is not None and rescue_start_min <= ideal < cooldown_end
        if ideal >= not_before_min - 1e-6 and not inside_rescue:
            targets.append(_Target(ideal, "normal"))
        ideal += interval_min
    targets.sort(key=lambda target: (target.minute, target.kind != "normal"))
    return targets


def _place_feed(
    segments: list[Segment],
    ideal_min: float,
    prefer_safe: bool,
) -> tuple[Segment, float] | None:
    """Pick a segment overlapping [ideal-6, ideal+6] and the time to use on it.

    Safe feeds prefer green, then yellow, then red, and only then the smallest
    clock shift. A water sip ignores zone and stays as close to the ideal time
    as it can.
    """

    window_lo = ideal_min - WINDOW_MIN
    window_hi = ideal_min + WINDOW_MIN
    candidates: list[tuple[tuple[float, float, int], Segment, float]] = []
    for segment in segments:
        overlap_lo = max(segment.start_minute, window_lo)
        overlap_hi = min(segment.end_minute, window_hi)
        if overlap_lo > overlap_hi:
            continue
        when = min(max(ideal_min, overlap_lo), overlap_hi)
        zone_rank = _zone_rank(segment.zone) if prefer_safe else 0.0
        rank = (zone_rank, abs(when - ideal_min), float(segment.index))
        candidates.append((rank, segment, when))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, segment, when = candidates[0]
    return segment, when


def _dose_for_target(
    *,
    target: _Target,
    zone: DigestiveZone,
    grade_percent: float,
    shift_min: float,
    athlete: AthleteInput,
    gentle_left: int,
) -> _Dose:
    """Decide carbs, fluid, sodium, and the sentence the runner should read.

    A window never receives carbs skipped by an earlier window.
    """

    hours = athlete.feed_interval_min / 60.0
    base_carbs = athlete.carbs_g_per_hour * hours
    ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    base_fluid = ml_per_hour * hours
    base_sodium = sodium_per_hour * hours
    grade_text = f"{grade_percent:+.1f}%"

    if target.kind == "rescue_nil":
        return _Dose(
            0.0,
            RESCUE_SIP_FLUID_ML,
            RESCUE_SIP_SODIUM_MG,
            FuelForm.rescue_sip,
            gentle_left,
            "GI Rescue: no calories yet. Take a small water and salt sip only.",
        )
    if target.kind == "rescue_ease":
        if zone == DigestiveZone.red:
            return _Dose(
                0.0,
                RESCUE_SIP_FLUID_ML,
                RESCUE_SIP_SODIUM_MG,
                FuelForm.rescue_sip,
                gentle_left,
                f"GI Rescue: grade {grade_text} is still red, so skip calories and sip.",
            )
        return _Dose(
            RESCUE_EASE_CARBS_G,
            RESCUE_EASE_FLUID_ML,
            RESCUE_EASE_SODIUM_MG,
            FuelForm.liquid,
            gentle_left,
            f"GI Rescue: {RESCUE_EASE_CARBS_G:.0f} g of liquid carb while the stomach settles.",
        )

    if zone == DigestiveZone.green:
        carbs = base_carbs
        form = FuelForm.gel
        reason = _green_reason(shift_min, grade_text, carbs)
    elif zone == DigestiveZone.yellow:
        carbs = base_carbs * 0.5
        form = FuelForm.liquid
        reason = _yellow_reason(shift_min, grade_text, carbs)
    else:
        carbs = min(RED_MICRO_DOSE_G, base_carbs)
        forfeited = base_carbs - carbs
        form = FuelForm.micro_dose
        reason = (
            f"No green or yellow ground inside +/-{WINDOW_MIN:.0f} min ({grade_text}). "
            f"Liquid carb micro-dose: {carbs:.0f} g from drink mix, in 2-3 small sips over 5 minutes."
        )
        if forfeited >= 1:
            reason += f" The other {forfeited:.0f} g in this window is forfeited."

    if gentle_left > 0 and carbs > 0:
        carbs *= 0.5
        gentle_left -= 1
        reason += " Half-size while GI Rescue eases off."

    return _Dose(carbs, base_fluid, base_sodium, form, gentle_left, reason)


def _green_reason(shift_min: float, grade_text: str, carbs: float) -> str:
    if abs(shift_min) < 0.25:
        return f"On time. Grade {grade_text} is green, so take a {carbs:.0f} g gel."
    return (
        f"Moved {shift_min:+.1f} min onto a green segment ({grade_text}). "
        f"Take a {carbs:.0f} g gel."
    )


def _yellow_reason(shift_min: float, grade_text: str, carbs: float) -> str:
    if abs(shift_min) < 0.25:
        text = f"On time, but grade {grade_text} is yellow."
    else:
        text = f"Moved {shift_min:+.1f} min onto a yellow segment ({grade_text})."
    return text + f" Half dose as liquid ({carbs:.0f} g). Nothing from this window is carried forward."


def _truncate_front(
    segments: list[Segment],
    course: list[CoursePoint],
    distance_m: float,
) -> list[Segment]:
    """Drop finished slices and shorten the slice the runner is currently in.

    The shortened slice keeps its parent's grade and zone so the race clock
    stays continuous. Rebuilding the grade from only the leftover metres would
    shift every later split.
    """

    kept: list[Segment] = []
    for segment in segments:
        if segment.end_distance_m <= distance_m + 1:
            continue
        if segment.start_distance_m >= distance_m - 1:
            kept.append(segment)
            continue
        fraction_done = (distance_m - segment.start_distance_m) / segment.distance_m
        fraction_done = min(max(fraction_done, 0.0), 1.0)
        location = location_at_distance(course, distance_m)
        remaining_m = segment.end_distance_m - distance_m
        kept.append(
            segment.model_copy(
                update={
                    "start_distance_m": distance_m,
                    "distance_m": remaining_m,
                    "start_lat": location.lat,
                    "start_lon": location.lon,
                    "elevation_start_m": location.elevation_m,
                    "elevation_gain_m": segment.elevation_gain_m * (1 - fraction_done),
                    "elevation_loss_m": segment.elevation_loss_m * (1 - fraction_done),
                    "predicted_minutes": segment.predicted_minutes * (1 - fraction_done),
                    "start_minute": segment.start_minute + fraction_done * segment.predicted_minutes,
                    "predicted_pace_min_per_km": segment.predicted_pace_min_per_km,
                }
            )
        )
    return kept


def _minute_at_distance(segments: list[Segment], distance_m: float) -> float:
    if not segments:
        return 0.0
    if distance_m <= segments[0].start_distance_m:
        return segments[0].start_minute
    for segment in segments:
        if segment.start_distance_m <= distance_m <= segment.end_distance_m:
            span = segment.end_distance_m - segment.start_distance_m
            fraction = 0.0 if span <= 0 else (distance_m - segment.start_distance_m) / span
            return segment.start_minute + fraction * segment.predicted_minutes
    return segments[-1].end_minute


def _distance_at_minute(segment: Segment, minute: float) -> float:
    span = segment.predicted_minutes
    fraction = 0.0 if span <= 0 else (minute - segment.start_minute) / span
    fraction = min(max(fraction, 0.0), 1.0)
    return segment.start_distance_m + fraction * segment.distance_m


def _segment_index_at(segments: list[Segment], distance_m: float) -> int:
    for segment in segments:
        if segment.start_distance_m <= distance_m <= segment.end_distance_m:
            return segment.index
    if segments and distance_m > segments[-1].end_distance_m:
        return segments[-1].index
    return 0


def _rotating(names: tuple[str, ...], when: float) -> str:
    """Pick the next food in a rotation so the same sweet thing is not repeated."""

    return names[int(when // 30) % len(names)]


def _apply_food_preference(
    dose: _Dose,
    *,
    grade_percent: float,
    zone: DigestiveZone,
    when: float,
    finish_min: float,
    athlete: AthleteInput,
) -> tuple[str, str]:
    """Name a real food or a gel, and mark the flavor.

    Red micro-doses and rescue sips stay as they are. A race longer than
    3.5 hours moves green feeds onto savoury food once the clock is past
    both 3.5 hours and halfway.
    """

    if dose.carbs_g <= 0 or dose.form in {FuelForm.rescue_sip, FuelForm.sip, FuelForm.micro_dose}:
        return "", ""

    savoury_time = finish_min > PALATE_FATIGUE_MIN and when >= max(PALATE_FATIGUE_MIN, finish_min / 2.0)
    if zone == DigestiveZone.green and savoury_time:
        name = _rotating(("Salted rice cake", "Pretzels or chips", "PB and jam bite", "Boiled potato"), when)
        dose.form = FuelForm.real_food
        dose.reason += f" Flavor: Savoury/Salty. Take {name}."
        return name, "Savoury/Salty"

    preference = athlete.fuel_preference
    if zone == DigestiveZone.green and preference == "real_food_heavy":
        name = _rotating(("Energy bar", "Salted rice cake", "Fruit or dates", "Boiled potato"), when)
        dose.form = FuelForm.real_food
        dose.reason += f" Flavor: Sweet. Take {name}."
        return name, "Sweet"
    if zone == DigestiveZone.green and preference == "balanced_real_food":
        options = (
            ("Gel", FuelForm.gel, "Sweet"),
            ("Energy bar", FuelForm.real_food, "Sweet"),
            ("Fruit or dates", FuelForm.real_food, "Sweet"),
            ("Salted rice cake", FuelForm.real_food, "Savoury/Salty"),
        )
        name, form, flavor = options[int(when // 30) % len(options)]
        dose.form = form
        dose.reason += f" Flavor: {flavor}. Take {name}."
        return name, flavor
    if zone == DigestiveZone.green and preference == "gels_first" and abs(grade_percent) <= FLAT_GREEN_GRADE:
        dose.form = FuelForm.real_food
        dose.reason += " Flavor: Sweet. Flat green, so take an energy bar."
        return "Energy bar", "Sweet"
    if zone == DigestiveZone.yellow and preference == "real_food_heavy":
        dose.reason += " Flavor: Sweet. Isotonic gel, sipped."
        return "Isotonic gel", "Sweet"
    if zone == DigestiveZone.green and dose.form == FuelForm.gel:
        dose.reason += " Flavor: Sweet."
        return "", "Sweet"
    return "", ""


def _insert_aid_bites(
    stops: list[FuelStop],
    stations: list[AidStation],
    segments: list[Segment],
) -> list[FuelStop]:
    """Add a plate at each food station after 75 minutes, and lighten a nearby vest snack.

    The plate is eaten while stopped, so a red grade at the pin does not
    turn it into a climbing micro-dose.
    """

    if not segments:
        return stops
    bites: list[FuelStop] = []
    for station in stations:
        if not station.has_food:
            continue
        distance_m = station.distance_km * 1000.0
        if distance_m < segments[0].start_distance_m - 30:
            continue
        minute = _minute_at_distance(segments, distance_m)
        if minute < AID_FOOD_MIN_MINUTE:
            continue
        segment = next(
            (item for item in segments if item.start_distance_m <= distance_m <= item.end_distance_m),
            segments[-1],
        )
        bites.append(
            FuelStop(
                sequence=1,
                ideal_minute=minute,
                scheduled_minute=minute,
                shift_minutes=0.0,
                distance_km=station.distance_km,
                latitude=station.lat,
                longitude=station.lon,
                segment_index=segment.index,
                grade_percent=segment.grade_percent,
                zone=segment.zone,
                carbs_g=AID_BITE_CARBS_G,
                fluid_ml=150.0,
                sodium_mg=200.0,
                form=FuelForm.real_food,
                reason=AID_PLATE,
                product="Aid station plate",
                flavor="Savoury/Salty",
                at_aid_station=True,
            )
        )
        for stop in stops:
            if stop.at_aid_station or stop.carbs_g <= 0:
                continue
            if abs(stop.scheduled_minute - minute) > 15:
                continue
            stop.carbs_g = max(0.0, stop.carbs_g - AID_BITE_CARBS_G)
            stop.caffeine_mg = 0.0
            if stop.carbs_g < 5:
                stop.carbs_g = 0.0
                stop.form = FuelForm.sip
                stop.product = ""
                stop.reason = f"Skip the vest snack. {AID_PLATE}"
            else:
                stop.reason += f" The aid-station plate covers {AID_BITE_CARBS_G:.0f} g of this window."
            break
    merged = stops + bites
    merged.sort(key=lambda stop: (stop.scheduled_minute, stop.distance_km))
    for index, stop in enumerate(merged, start=1):
        stop.sequence = index
    return merged


def _wants_caffeine(segments: list[Segment], when: float, race_start: float, finish_min: float) -> bool:
    """True before a steep climb, and for every feed in the second half.

    The race clock runs from the start, so halfway is half of the finish
    minute. A plan that only covers the road ahead still uses that clock:
    once the runner is already past halfway, later gels may carry caffeine.
    """

    if when >= finish_min / 2.0 or race_start >= finish_min / 2.0:
        return True
    horizon = when + CLIMB_LOOKAHEAD_MIN
    for segment in segments:
        if segment.end_minute < when:
            continue
        if segment.start_minute > horizon:
            break
        if segment.grade_percent >= MAJOR_CLIMB_GRADE:
            return True
    return False


def _apply_locker(
    dose: _Dose,
    segments: list[Segment],
    when: float,
    race_start: float,
    finish_min: float,
    athlete: AthleteInput,
    caffeine_used: float,
) -> tuple[str, float, str]:
    """Name the locker product and add caffeine only when the cap allows it.

    An empty locker leaves the dose sentence unchanged, so a plan with no
    custom products still reads as a plain gel. Caffeine is never attached
    to a yellow sip, a red micro-dose, or a GI-rescue sip.
    """

    locker = athlete.locker
    if not locker or dose.form != FuelForm.gel or dose.carbs_g <= 0:
        return "", 0.0, dose.reason

    caffeinated = next((item for item in locker if item.kind == "gel" and item.caffeine_mg > 0), None)
    plain = next((item for item in locker if item.caffeine_mg <= 0), None)
    cap = athlete.weight_kg * CAFFEINE_MG_PER_KG
    wants = _wants_caffeine(segments, when, race_start, finish_min)
    if wants and caffeinated is not None and caffeine_used + caffeinated.caffeine_mg <= cap + 1e-6:
        used = caffeinated.caffeine_mg
        reason = (
            f"{dose.reason} Use {caffeinated.name} "
            f"({used:.0f} mg caffeine). "
            f"Total after this stop: {caffeine_used + used:.0f} / {cap:.0f} mg."
        )
        return caffeinated.name, used, reason

    blocked = ""
    if wants and caffeinated is not None:
        blocked = f" Caffeine cap is {cap:.0f} mg, so this stop stays uncaffeinated."
    if plain is not None:
        return plain.name, 0.0, f"{dose.reason} Use {plain.name}.{blocked}"
    return "", 0.0, dose.reason + blocked


def _pack_items(
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    locker: list[LockerItem] | None = None,
    stops: list[FuelStop] | None = None,
    preference: str = "gels_first",
) -> list[PackItem]:
    """Turn an already-buffered vest total into food, flasks, and salt.

    `carbs_g` is what goes in the vest after any aid-station plate is subtracted.
    An empty locker follows `preference`. A locker still packs the runner's own items.
    """

    if not locker:
        return _pack_for_preference(carbs_g, fluid_ml, sodium_mg, preference)

    items: list[PackItem] = []
    covered = 0.0
    for product in locker:
        if product.kind != "gel" or product.caffeine_mg <= 0:
            continue
        count = sum(1 for stop in (stops or []) if stop.product == product.name and stop.caffeine_mg > 0)
        if count <= 0:
            continue
        items.append(
            PackItem(
                item=product.name,
                quantity=count,
                detail=f"{product.carbs_g:.0f} g carbs and {product.caffeine_mg:.0f} mg caffeine each.",
            )
        )
        covered += count * product.carbs_g

    plain = next((item for item in locker if item.caffeine_mg <= 0 and item.kind == "gel"), None)
    if plain is None:
        plain = next((item for item in locker if item.caffeine_mg <= 0), None)
    remaining = max(0.0, carbs_g - covered)
    if plain is not None and remaining >= 1:
        quantity = math.ceil(remaining / plain.carbs_g)
        items.append(
            PackItem(
                item=plain.name,
                quantity=quantity,
                detail=f"{plain.carbs_g:.0f} g carbs each. The leg total already includes the 15% reserve.",
            )
        )
    elif remaining >= 1:
        items.extend(_pack_plain(remaining, 0, 0))

    if fluid_ml >= 1:
        flasks = math.ceil(fluid_ml / FLASK_ML)
        liquid = next((item for item in locker if item.kind == "liquid"), None)
        if liquid is None:
            items.append(PackItem(item="Soft flask 500 ml", quantity=flasks, detail="Water or mix for this leg."))
        else:
            items.append(
                PackItem(
                    item=liquid.name,
                    quantity=flasks,
                    detail=f"{liquid.carbs_g:.0f} g carbs per 500 ml. The leg fluid total already includes the 15% reserve.",
                )
            )
    if sodium_mg >= 1:
        capsules = math.ceil(sodium_mg / CAPSULE_SODIUM_MG)
        items.append(PackItem(item="Salt capsule", quantity=capsules, detail="200 mg sodium each."))
    return items


def _pack_for_preference(carbs_g: float, fluid_ml: float, sodium_mg: float, preference: str) -> list[PackItem]:
    """Split vest carbs into gels, bars, and savoury food, then add flasks and salt.

    A short vest under 60 g stays gels, so the small packing check still sees
    two 25 g gels and no bar.
    """

    items: list[PackItem] = []
    remaining = carbs_g
    if preference == "real_food_heavy":
        if remaining >= 22:
            items.append(PackItem(item="Isotonic gel", quantity=1, detail="22 g backup for yellow or red ground."))
            remaining -= 22
        rice = math.ceil((remaining * 0.5) / 20.0) if remaining >= 1 else 0
        rice_carbs = min(remaining, rice * 20.0)
        if rice:
            items.append(PackItem(item="Salted rice cake", quantity=rice, detail="20 g carbs each."))
            remaining -= rice_carbs
        if remaining >= 1:
            bars = math.ceil(remaining / 35.0)
            items.append(PackItem(item="Energy bar", quantity=bars, detail="35 g carbs each."))
    elif preference == "balanced_real_food":
        gel_carbs = remaining * 0.4
        bar_carbs = remaining * 0.3
        rice_carbs = remaining - gel_carbs - bar_carbs
        gels = math.ceil(gel_carbs / GEL_CARBS_G) if gel_carbs >= 1 else 0
        bars = math.ceil(bar_carbs / 35.0) if bar_carbs >= 1 else 0
        rice = math.ceil(rice_carbs / 20.0) if rice_carbs >= 1 else 0
        if gels:
            items.append(PackItem(item="Gel", quantity=gels, detail="25 g carbs each."))
        if bars:
            items.append(PackItem(item="Energy bar", quantity=bars, detail="35 g carbs each."))
        if rice:
            items.append(PackItem(item="Salted rice cake", quantity=rice, detail="20 g carbs each."))
    else:
        if remaining >= 60:
            items.append(PackItem(item="Energy bar", quantity=1, detail="35 g carbs. Flat-green spare so the vest is not only gels."))
            remaining -= 35
        if remaining >= 1:
            gels = math.ceil(remaining / GEL_CARBS_G)
            items.append(PackItem(item="Gel", quantity=gels, detail="25 g carbs each. The leg total already includes the 15% reserve."))
    items.extend(_pack_plain(0, fluid_ml, sodium_mg))
    return items


def _pack_plain(carbs_g: float, fluid_ml: float, sodium_mg: float) -> list[PackItem]:
    """Default pack: 25 g gels, 500 ml flasks, and 200 mg salt capsules."""

    items: list[PackItem] = []
    if carbs_g >= 1:
        gels = math.ceil(carbs_g / GEL_CARBS_G)
        items.append(PackItem(item="Gel", quantity=gels, detail="25 g carbs each. The leg total already includes the 15% reserve."))
    if fluid_ml >= 1:
        flasks = math.ceil(fluid_ml / FLASK_ML)
        items.append(PackItem(item="Soft flask 500 ml", quantity=flasks, detail="Water or mix for this leg."))
    if sodium_mg >= 1:
        capsules = math.ceil(sodium_mg / CAPSULE_SODIUM_MG)
        items.append(PackItem(item="Salt capsule", quantity=capsules, detail="200 mg sodium each."))
    return items


def _totals(stops: list[FuelStop], athlete: AthleteInput) -> PlanTotals:
    """Count stops that landed on time, moved inside the window, or withheld calories."""

    on_time = 0
    shifted = 0
    deferred = 0
    for stop in stops:
        calories_withheld = stop.form == FuelForm.sip or (
            stop.form == FuelForm.rescue_sip and stop.carbs_g == 0 and "red" in stop.reason
        )
        if calories_withheld:
            deferred += 1
        elif abs(stop.shift_minutes) < 0.25:
            on_time += 1
        else:
            shifted += 1
    return PlanTotals(
        carbs_g=sum(stop.carbs_g for stop in stops),
        fluid_ml=sum(stop.fluid_ml for stop in stops),
        sodium_mg=sum(stop.sodium_mg for stop in stops),
        carbs_g_per_kg_per_hour=athlete.carbs_g_per_hour / athlete.weight_kg,
        stops_on_time=on_time,
        stops_shifted=shifted,
        stops_deferred=deferred,
        caffeine_mg=sum(stop.caffeine_mg for stop in stops),
        caffeine_cap_mg=athlete.weight_kg * CAFFEINE_MG_PER_KG,
    )


def _zone_counts(segments: list[Segment]) -> dict[str, int]:
    counts = {zone.value: 0 for zone in DigestiveZone}
    for segment in segments:
        counts[segment.zone.value] += 1
    return counts


def _distance_warnings(declared_km: float, computed_km: float) -> list[str]:
    if declared_km <= 0:
        return []
    gap = abs(computed_km - declared_km) / declared_km
    if gap > 0.10:
        return [
            f"Track length is {computed_km:.1f} km, which differs from the stated {declared_km:.1f} km by more than 10%."
        ]
    return []


def _stations_on_course(stations: list[AidStation], total_m: float, warnings: list[str]) -> list[AidStation]:
    kept: list[AidStation] = []
    for station in sorted(stations, key=lambda item: item.distance_km):
        if station.distance_km * 1000.0 > total_m + 50:
            warnings.append(f"Aid station {station.name} is past the end of the track and was ignored.")
            continue
        kept.append(station)
    return kept


def _zone_rank(zone: DigestiveZone) -> float:
    return {DigestiveZone.green: 0.0, DigestiveZone.yellow: 1.0, DigestiveZone.red: 2.0}[zone]


def _to_local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    metres_per_deg_lon = METERS_PER_DEG_LAT * math.cos(math.radians(origin_lat))
    x = (lon - origin_lon) * metres_per_deg_lon
    y = (lat - origin_lat) * METERS_PER_DEG_LAT
    return x, y


def _recalibrate_message(snap: SnapResult, minutes_behind: float, gi_rescue: bool) -> str:
    if snap.predicted_minute < MIN_PACE_SAMPLE_MIN:
        pace_text = "Still near the start, so the original effort plan is unchanged."
    elif minutes_behind > 1:
        pace_text = (
            f"You are {minutes_behind:.0f} min behind the effort plan. "
            "Remaining splits were stretched to match your current pace."
        )
    elif minutes_behind < -1:
        pace_text = (
            f"You are {-minutes_behind:.0f} min ahead of the effort plan. "
            "Remaining splits were compressed."
        )
    else:
        pace_text = "You are on the planned effort. Terrain windows are unchanged."

    if not snap.on_course:
        pace_text += f" GPS is {snap.off_course_m:.0f} m off the track; the nearest point on course was used."
    if gi_rescue:
        pace_text += " GI Rescue is on: solids are paused, and the next reminders are small fluid-and-salt sips."
    return pace_text


def _format_pack_item(item: PackItem) -> str:
    if item.item.startswith("Drink mix"):
        return f"{item.quantity} g drink mix"
    return f"{item.quantity} x {item.item}"


def _format_hours(minutes: float) -> str:
    hours = int(minutes // 60)
    mins = int(round(minutes - hours * 60))
    if mins == 60:
        hours += 1
        mins = 0
    return f"{hours} h {mins:02d} min"


def _print_plan(plan: RacePlan) -> None:
    zones = plan.zone_counts
    print(f"\n=== {plan.race_name} ===")
    print(
        f"Distance {plan.distance_km:.2f} km | gain {plan.elevation_gain_m:.0f} m | "
        f"predicted {_format_hours(plan.predicted_duration_min)} | "
        f"effort {plan.athlete.flat_pace_min_per_km:.1f} min/km GAP"
    )
    print(
        f"Segments {len(plan.segments)} | "
        f"green {zones.get('green', 0)} | yellow {zones.get('yellow', 0)} | red {zones.get('red', 0)}"
    )
    print(
        f"Fuel stops {len(plan.fuel_stops)} | "
        f"carbs {plan.totals.carbs_g:.0f} g | fluid {plan.totals.fluid_ml:.0f} ml | "
        f"sodium {plan.totals.sodium_mg:.0f} mg | "
        f"on time {plan.totals.stops_on_time} | shifted {plan.totals.stops_shifted} | "
        f"deferred {plan.totals.stops_deferred}"
    )
    if plan.warnings:
        for warning in plan.warnings:
            print(f"  warning: {warning}")
    print("First fuel stops:")
    for stop in plan.fuel_stops[:6]:
        print(
            f"  #{stop.sequence:<2} t={stop.scheduled_minute:6.1f} min ({stop.shift_minutes:+5.1f}) "
            f"km {stop.distance_km:5.1f}  {stop.zone.value:<6} {stop.form.value:<11} "
            f"{stop.carbs_g:5.1f} g  {stop.reason}"
        )
    print("Aid-station legs:")
    for leg in plan.aid_legs:
        pack = ", ".join(_format_pack_item(item) for item in leg.pack) or "nothing extra"
        print(
            f"  {leg.from_name} -> {leg.to_name}: {leg.distance_km:.1f} km, "
            f"{leg.predicted_minutes:.0f} min, pack [{pack}]"
        )


def _demo_segment(
    index: int,
    start_min: float,
    end_min: float,
    zone: DigestiveZone,
    grade: float,
) -> Segment:
    """A tiny segment used only by the terminal self-check."""

    distance_m = 500.0
    minutes = end_min - start_min
    return Segment(
        index=index,
        start_distance_m=index * distance_m,
        end_distance_m=(index + 1) * distance_m,
        distance_m=distance_m,
        start_lat=42.6,
        start_lon=23.3,
        end_lat=42.6,
        end_lon=23.3,
        elevation_start_m=1000,
        elevation_end_m=1000 + grade * 5,
        elevation_gain_m=max(grade, 0) * 5,
        elevation_loss_m=max(-grade, 0) * 5,
        grade_percent=grade,
        zone=zone,
        cost_factor=gap_cost_factor(grade),
        predicted_minutes=minutes,
        start_minute=start_min,
        end_minute=end_min,
        predicted_pace_min_per_km=minutes / 0.5,
        gap_min_per_km=(minutes / 0.5) / gap_cost_factor(grade),
    )


def _check_invariants() -> None:
    """Fail the process if the sports-science rules drift."""

    one_degree_m = haversine_m(42.0, 23.0, 43.0, 23.0)
    if not 110_900 < one_degree_m < 111_400:
        raise AssertionError(f"Haversine 1 degree latitude was {one_degree_m:.1f} m")
    if haversine_m(42.0, 23.0, 42.0, 23.0) != 0.0:
        raise AssertionError("Haversine of identical points must be 0")

    smoothed = smooth_elevation([0, 0, 0, 100, 0, 0, 0], window=5)
    if smoothed[3] != 20:
        raise AssertionError(f"5-point smooth of a spike should be 20, got {smoothed[3]}")

    if gap_cost_factor(0) != 1.0:
        raise AssertionError("Flat GAP cost factor must be exactly 1")
    if not (gap_cost_factor(10) > 1.0 > gap_cost_factor(-10)):
        raise AssertionError("Uphill GAP must cost more than flat, and mild downhill less")
    if classify_zone(5.0) != DigestiveZone.green or classify_zone(5.9) != DigestiveZone.yellow:
        raise AssertionError("+5.0% must be green and +5.9% must be yellow")
    if classify_zone(10.0) != DigestiveZone.yellow or classify_zone(10.5) != DigestiveZone.red:
        raise AssertionError("+10.0% must be yellow and +10.5% must be red")
    if classify_zone(-8.0) != DigestiveZone.green or classify_zone(-12.0) != DigestiveZone.yellow:
        raise AssertionError("-8% must be green and -12% must be yellow")
    if classify_zone(-12.1) != DigestiveZone.red:
        raise AssertionError("A descent steeper than -12% must be red")
    cool_ml, _cool_na = sweat_rates(14.9, 70)
    mild_ml, mild_na = sweat_rates(18, 70)
    hot_ml, hot_na = sweat_rates(25.1, 70)
    if cool_ml != 450 + 3 * 5 or mild_ml != 670 or hot_ml != 850 + 5 * 5:
        raise AssertionError(f"Sweat rate ml/h was cool {cool_ml}, mild {mild_ml}, hot {hot_ml}")
    if not math.isclose(mild_na, 700 * 0.670) or not math.isclose(hot_na, 950 * hot_ml / 1000):
        raise AssertionError("Sodium must be the band's mg/L times hourly litres")

    # Window: ideal minute 8 overlaps a red slice ending at 10 and a green slice from 10.
    # The gel must move onto the green slice, and the shift must stay within 6 minutes.
    red = _demo_segment(0, 0, 10, DigestiveZone.red, 16)
    green = _demo_segment(1, 10, 20, DigestiveZone.green, 1)
    later_red = _demo_segment(2, 20, 40, DigestiveZone.red, 15)
    placed = _place_feed([red, green, later_red], ideal_min=8, prefer_safe=True)
    if placed is None or placed[0].zone != DigestiveZone.green:
        raise AssertionError("Feed at minute 8 should move onto the green segment")
    if abs(placed[1] - 8) > WINDOW_MIN:
        raise AssertionError("Window shift exceeded 6 minutes")

    only_red = _place_feed([red], ideal_min=5, prefer_safe=True)
    if only_red is None or only_red[0].zone != DigestiveZone.red:
        raise AssertionError("A fully red window should still return a sip location")

    athlete = AthleteInput(weight_kg=70, temperature_c=18, carbs_g_per_hour=60, feed_interval_min=30)
    window_carbs = 30.0
    red_dose = _dose_for_target(
        target=_Target(5, "normal"),
        zone=DigestiveZone.red,
        grade_percent=16,
        shift_min=0,
        athlete=athlete,
        gentle_left=0,
    )
    if red_dose.carbs_g != RED_MICRO_DOSE_G or red_dose.form != FuelForm.micro_dose:
        raise AssertionError("A red-only window must take a 15 g liquid micro-dose")
    if "forfeited" not in red_dose.reason or "15 g" not in red_dose.reason:
        raise AssertionError("A red window must forfeit the rest of its own carbs")
    green_dose = _dose_for_target(
        target=_Target(35, "normal"),
        zone=DigestiveZone.green,
        grade_percent=1,
        shift_min=0,
        athlete=athlete,
        gentle_left=0,
    )
    if green_dose.carbs_g != window_carbs:
        raise AssertionError("The next green window must stay at one dose, with no carb debt")
    packed = _pack_items(0.72 * 60 * PACK_BUFFER, 500, 400)
    gels = next(item.quantity for item in packed if item.item == "Gel")
    if gels != 2:
        raise AssertionError(f"A 0.72 h leg at 60 g/h with a 15% buffer must pack 2 gels, got {gels}")

    rescue = _dose_for_target(
        target=_Target(0, "rescue_nil"),
        zone=DigestiveZone.green,
        grade_percent=0,
        shift_min=0,
        athlete=athlete,
        gentle_left=2,
    )
    if rescue.carbs_g != 0 or rescue.form != FuelForm.rescue_sip:
        raise AssertionError("GI Rescue must open with a zero-carb sip")


def run_self_check() -> None:
    """Print both sample races and check the math. Run: python -m backend.fuel_engine"""

    _check_invariants()
    races = load_race_catalog()
    if len(races) < 4:
        raise AssertionError("races.json should contain the four Vitosha GPX races")

    athlete_42 = AthleteInput(weight_kg=68, temperature_c=18, flat_pace_min_per_km=7.5, carbs_g_per_hour=60)
    athlete_100 = AthleteInput(weight_kg=68, temperature_c=18, flat_pace_min_per_km=9.0, carbs_g_per_hour=70)

    expected_ids = (
        "salomon-vitosha-12",
        "salomon-vitosha-27",
        "salomon-vitosha-40",
        "vitosha-100",
    )
    by_id = {race.id: race for race in races}
    for race_id in expected_ids:
        if race_id not in by_id:
            raise AssertionError(f"races.json is missing {race_id}")
        if not by_id[race_id].gpx_file:
            raise AssertionError(f"{race_id} should point at a GPX file")

    athletes_by_race = {
        "salomon-vitosha-27": athlete_42,
        "vitosha-100": athlete_100,
    }
    for race_id, athlete in athletes_by_race.items():
        race = attach_track(by_id[race_id])
        by_id[race_id] = race
        if len(race.profile) < 2:
            raise AssertionError(f"{race.name} GPX did not load")
        measured_km = prepare_course(race.profile)[-1].distance_m / 1000.0
        if abs(measured_km - race.distance_km) / race.distance_km > 0.02:
            raise AssertionError(f"{race.name} length {measured_km:.2f} km disagrees with the catalog")
        plan = generate_plan_for_race(race, athlete)
        _print_plan(plan)
        _assert_plan(plan, require_red=race_id != "vitosha-100")
        if race_id == "salomon-vitosha-27" and not any(leg.station_meal for leg in plan.aid_legs):
            raise AssertionError("The 27 km course should plate food at a station after 75 minutes")
        if race_id == "vitosha-100" and not any(stop.flavor == "Savoury/Salty" for stop in plan.fuel_stops):
            raise AssertionError("A 15 hour race should rotate onto savoury food after 3.5 hours")

    for race_id in ("salomon-vitosha-12", "salomon-vitosha-40"):
        race = attach_track(by_id[race_id])
        measured_km = prepare_course(race.profile)[-1].distance_m / 1000.0
        if abs(measured_km - race.distance_km) / race.distance_km > 0.02:
            raise AssertionError(f"{race.name} length {measured_km:.2f} km disagrees with the catalog")

    gpx = """<?xml version="1.0" encoding="UTF-8"?>
    <gpx version="1.1" creator="race-fuel-copilot">
      <trk><name>smoke</name><trkseg>
        <trkpt lat="42.6250" lon="23.3110"><ele>900</ele></trkpt>
        <trkpt lat="42.6290" lon="23.3110"><ele>980</ele></trkpt>
        <trkpt lat="42.6330" lon="23.3110"><ele>1040</ele></trkpt>
      </trkseg></trk>
    </gpx>
    """
    parsed = parse_gpx_xml(gpx)
    if len(parsed) != 3:
        raise AssertionError("GPX smoke test did not return 3 points")
    gpx_plan = generate_plan(
        race_id="uploaded",
        race_name="Uploaded GPX",
        declared_distance_km=1,
        aid_stations=[],
        profile=parsed,
        athlete=AthleteInput(),
    )
    if not gpx_plan.segments:
        raise AssertionError("GPX course produced no segments")

    locker_athlete = athlete_42.model_copy(
        update={
            "locker": [
                LockerItem(name="Standard Gel", carbs_g=25, caffeine_mg=0, kind="gel"),
                LockerItem(name="Caffeinated Gel", carbs_g=25, caffeine_mg=100, kind="gel"),
            ]
        }
    )
    caffeinated = generate_plan_for_race(by_id["salomon-vitosha-27"], locker_athlete)
    cap = locker_athlete.weight_kg * CAFFEINE_MG_PER_KG
    if caffeinated.totals.caffeine_mg < 100:
        raise AssertionError("A course with a climb should schedule at least one caffeinated gel")
    if caffeinated.totals.caffeine_mg > cap + 0.1:
        raise AssertionError(f"Caffeine {caffeinated.totals.caffeine_mg:.0f} mg exceeds the {cap:.0f} mg cap")
    if abs(caffeinated.totals.caffeine_cap_mg - cap) > 0.1:
        raise AssertionError("Plan totals must publish the 5 mg/kg caffeine cap")
    for stop in caffeinated.fuel_stops:
        if stop.caffeine_mg > 0 and stop.form != FuelForm.gel:
            raise AssertionError("Caffeine belongs on a gel feed, not a sip or a red micro-dose")
    packed_names = [item.item for leg in caffeinated.aid_legs for item in leg.pack]
    if "Caffeinated Gel" not in packed_names:
        raise AssertionError("The packing list must include the caffeinated gels the schedule placed")

    race = by_id["salomon-vitosha-27"]
    course = prepare_course(race.profile)
    # A dense GPS file has many points in the first minutes. Sample mid-race
    # so the pace ratio is allowed to move (it stays 1.0 near the start).
    sample = course[len(course) // 3]
    baseline = build_timed_segments(slice_segments(course), athlete_42)
    predicted = _minute_at_distance(baseline, sample.distance_m)
    elapsed = predicted * 1.2
    updated = recalibrate(
        race_id=race.id,
        race_name=race.name,
        declared_distance_km=race.distance_km,
        aid_stations=race.aid_stations,
        profile=race.profile,
        athlete=athlete_42,
        latitude=sample.lat,
        longitude=sample.lon,
        elapsed_min=elapsed,
        gi_rescue=False,
    )
    print(
        f"\nRecalibrated {race.name} at km {updated.snap.distance_along_km:.1f}: "
        f"off course {updated.snap.off_course_m:.1f} m, pace ratio {updated.snap.pace_ratio:.2f}"
    )
    print(f"  {updated.message}")
    if updated.snap.off_course_m > 5:
        raise AssertionError("Snapping a track point should land within 5 m")
    if abs(updated.snap.distance_along_m - sample.distance_m) > 50:
        raise AssertionError("Snap distance drifted away from the sample point")
    if not math.isclose(updated.snap.pace_ratio, 1.2, rel_tol=0.02):
        raise AssertionError(f"Expected pace ratio near 1.2, got {updated.snap.pace_ratio}")
    if updated.remaining.fuel_stops and updated.remaining.fuel_stops[0].scheduled_minute + 1e-6 < elapsed:
        raise AssertionError("Recalibrated plan still contains a feed in the past")

    rescue = recalibrate(
        race_id=race.id,
        race_name=race.name,
        declared_distance_km=race.distance_km,
        aid_stations=race.aid_stations,
        profile=race.profile,
        athlete=athlete_42,
        latitude=sample.lat,
        longitude=sample.lon,
        elapsed_min=elapsed,
        gi_rescue=True,
    )
    first = rescue.remaining.fuel_stops[0]
    print(
        f"GI Rescue first stop at t={first.scheduled_minute:.1f} min: "
        f"{first.form.value}, {first.carbs_g:.0f} g. {first.reason}"
    )
    if first.carbs_g != 0 or first.form != FuelForm.rescue_sip:
        raise AssertionError("GI Rescue should open with a zero-carb sip")

    print("\nSelf-check passed.")


def _vest_carbs_packed(pack: list[PackItem]) -> float:
    """Carbs represented by vest food lines. Flasks and salt are not food."""

    servings = {
        "Gel": GEL_CARBS_G,
        "Energy bar": 35.0,
        "Isotonic gel": 22.0,
        "Salted rice cake": 20.0,
        "Fruit or dates": 25.0,
        "Pretzels or chips": 22.0,
        "PB and jam bite": 25.0,
        "Boiled potato": 25.0,
    }
    return sum(item.quantity * servings.get(item.item, 0.0) for item in pack)


def _assert_plan(plan: RacePlan, require_red: bool = True) -> None:
    segment_distance = sum(segment.distance_m for segment in plan.segments)
    course_distance = plan.distance_km * 1000.0
    # A full plan's slices partition the track. Remaining plans start mid-slice.
    if plan.clock_start_min == 0 and abs(segment_distance - course_distance) > 1:
        raise AssertionError(f"Segments do not cover the course ({segment_distance:.1f} vs {course_distance:.1f})")
    if plan.elevation_gain_m < 500:
        raise AssertionError(f"{plan.race_name} gain looks flat: {plan.elevation_gain_m:.0f} m")
    if plan.zone_counts.get("green", 0) < 1:
        raise AssertionError(f"{plan.race_name} should include green terrain")
    if require_red and plan.zone_counts.get("red", 0) < 1:
        raise AssertionError(f"{plan.race_name} should include red terrain")
    if len(plan.fuel_stops) < 3:
        raise AssertionError(f"{plan.race_name} produced too few fuel stops")
    for stop in plan.fuel_stops:
        if abs(stop.shift_minutes) > WINDOW_MIN + 1e-6:
            raise AssertionError(f"Shift of {stop.shift_minutes} min is outside the +/-6 min window")
        if (
            stop.zone == DigestiveZone.red
            and not stop.at_aid_station
            and stop.form not in {FuelForm.micro_dose, FuelForm.rescue_sip, FuelForm.sip}
        ):
            raise AssertionError("A red segment must be a micro-dose or a rescue sip")
        if stop.form == FuelForm.micro_dose and stop.carbs_g > RED_MICRO_DOSE_G:
            raise AssertionError("A red micro-dose is larger than 15 g")
        if stop.zone == DigestiveZone.yellow and stop.form == FuelForm.gel:
            raise AssertionError("A yellow segment was assigned a gel")
        if "carried over" in stop.reason or "held back" in stop.reason:
            raise AssertionError("A fuel window still carries carbohydrate debt")
    if not plan.aid_legs:
        raise AssertionError("Plan is missing aid-station legs")
    if plan.clock_start_min == 0:
        opening = plan.aid_legs[0]
        if opening.from_name != "Start" or opening.from_km != 0:
            raise AssertionError("A full plan must start at 0.0 km (Start)")
    fluid_per_hour, sodium_per_hour = sweat_rates(plan.athlete.temperature_c, plan.athlete.weight_kg)
    for leg in plan.aid_legs:
        hours = leg.predicted_minutes / 60.0
        expected_carbs = hours * plan.athlete.carbs_g_per_hour * PACK_BUFFER
        expected_fluid = hours * fluid_per_hour * PACK_BUFFER
        expected_sodium = hours * sodium_per_hour * PACK_BUFFER
        if abs(leg.carbs_g - expected_carbs) > 0.01 or abs(leg.fluid_ml - expected_fluid) > 0.01:
            raise AssertionError(f"{leg.from_name} -> {leg.to_name} is not duration x rate x 1.15")
        if abs(leg.sodium_mg - expected_sodium) > 0.01:
            raise AssertionError(f"{leg.from_name} sodium pack does not follow the temperature formula")
        if abs((leg.vest_carbs_g + leg.station_carbs_g) - leg.carbs_g) > 0.05:
            raise AssertionError(f"{leg.from_name} vest plus station plate does not add up to the leg carbs")
        if leg.station_carbs_g > AID_BITE_CARBS_G + 0.1:
            raise AssertionError("An aid-station plate is larger than 28 g")
        if not plan.athlete.locker and leg.vest_carbs_g >= 1:
            covered = _vest_carbs_packed(leg.pack)
            if covered + 1 < leg.vest_carbs_g:
                raise AssertionError(
                    f"{leg.from_name} vest packs {covered:.0f} g for {leg.vest_carbs_g:.0f} g"
                )


if __name__ == "__main__":
    run_self_check()
