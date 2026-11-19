"""Race-clock segments and the minute where a feed lands."""

from __future__ import annotations

from backend.gpx_parser import (
    CoursePoint,
    CourseSegment,
    location_at_distance,
)

from backend.models import (
    AidStation,
    AthleteInput,
    FuelForm,
    FuelStop,
    Segment,
    StopReason,
)

from backend.engine.reasons import shift_reasons

from backend.engine.constants import WINDOW_MIN

from backend.engine.gap import gap_cost_factor

from backend.engine.zones import (
    _zone_rank,
    classify_zone,
    zone_edges_for,
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
    edges = zone_edges_for(athlete)
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
                zone=classify_zone(segment.grade_percent, *edges),
                cost_factor=factor,
                predicted_minutes=minutes,
                start_minute=start_minute,
                end_minute=clock_min,
                predicted_pace_min_per_km=predicted_pace,
                gap_min_per_km=predicted_pace / factor,
            )
        )
    return timed


def _stop_on_clock(
    segments: list[Segment],
    course: list[CoursePoint],
    ideal: float,
    *,
    prefer_safe: bool,
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    form: FuelForm,
    reason: str,
    product: str,
    flavor: str,
    extra_reasons: list[StopReason] | None = None,
) -> FuelStop | None:
    """One stop near `ideal`, staying inside the terrain window when it can."""

    if not segments:
        return None
    placed = _place_feed(segments, ideal, prefer_safe=prefer_safe)
    if placed is None:
        segment = min(segments, key=lambda item: abs(item.start_minute - ideal))
        when = min(max(ideal, segment.start_minute), segment.end_minute)
    else:
        segment, when = placed
    distance_m = _distance_at_minute(segment, when)
    location = location_at_distance(course, distance_m)
    return FuelStop(
        sequence=1,
        ideal_minute=ideal,
        scheduled_minute=when,
        shift_minutes=when - ideal,
        distance_km=distance_m / 1000.0,
        latitude=location.lat,
        longitude=location.lon,
        segment_index=segment.index,
        grade_percent=segment.grade_percent,
        zone=segment.zone,
        carbs_g=carbs_g,
        fluid_ml=fluid_ml,
        sodium_mg=sodium_mg,
        form=form,
        reason=reason,
        product=product,
        flavor=flavor,
        reasons=shift_reasons(ideal, when, segment.grade_percent, segment.index) + list(extra_reasons or []),
    )


def _average_grade(segments: list[Segment], from_m: float, to_m: float) -> float:
    """Net grade of one aid leg, in percent. Negative means a descent."""

    rise = 0.0
    run = 0.0
    for segment in segments:
        overlap_start = max(segment.start_distance_m, from_m)
        overlap_end = min(segment.end_distance_m, to_m)
        if overlap_end <= overlap_start:
            continue
        fraction = (overlap_end - overlap_start) / segment.distance_m
        rise += (segment.elevation_end_m - segment.elevation_start_m) * fraction
        run += overlap_end - overlap_start
    if run < 1:
        return 0.0
    return rise / run * 100.0


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


def _approach_minutes(
    segments: list[Segment],
    stations: list[AidStation],
    distance_m: float,
) -> tuple[float, float]:
    """Minutes spent on the leg that arrives at `distance_m`, and the arrival clock."""

    previous_m = segments[0].start_distance_m if segments else 0.0
    for station in sorted(stations, key=lambda item: item.distance_km):
        station_m = station.distance_km * 1000.0
        if station_m >= distance_m - 1:
            break
        if station_m > previous_m:
            previous_m = station_m
    start = _minute_at_distance(segments, previous_m)
    end = _minute_at_distance(segments, distance_m)
    return max(0.0, end - start), end
