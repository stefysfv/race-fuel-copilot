"""Snap a live fix to the track and rebuild the plan still ahead."""

from __future__ import annotations

import math

from backend.gpx_parser import (
    CoursePoint,
    location_at_distance,
    prepare_course,
    slice_segments,
)

from backend.models import (
    AidStation,
    AthleteInput,
    CustomAidInput,
    PackedVestItem,
    RecalibrateResponse,
    SnapResult,
    TrackPoint,
)

from backend.engine.constants import (
    MAX_PACE_RATIO,
    METERS_PER_DEG_LAT,
    MIN_PACE_RATIO,
    MIN_PACE_SAMPLE_MIN,
    OFF_COURSE_M,
)

from backend.engine.scheduler import generate_plan

from backend.engine.timing import (
    _minute_at_distance,
    _segment_index_at,
    build_timed_segments,
)


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
    packed_vest_items: list[PackedVestItem] | None = None,
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
        packed_vest_items=packed_vest_items,
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


def _pace_ratio(elapsed_min: float, predicted_min: float) -> float:
    if predicted_min < MIN_PACE_SAMPLE_MIN:
        return 1.0
    raw = elapsed_min / predicted_min
    return min(MAX_PACE_RATIO, max(MIN_PACE_RATIO, raw))












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
