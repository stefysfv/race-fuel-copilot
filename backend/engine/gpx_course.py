"""Rules for an uploaded GPX. Catalog races do not use this module.

The reader in `gpx_parser` still returns every track point with a missing
elevation stored as 0 m. An upload is inspected here: friendly errors, a
track choice, elevation repair, an 8 m point step, and aid waypoints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from backend.engine.recalibrate import snap_to_track
from backend.gpx_parser import (
    RawPath,
    RawSample,
    RawWaypoint,
    haversine_m,
    location_at_distance,
    prepare_course,
    read_gpx,
    slice_segments,
)
from backend.models import GPX_XML_MAX_CHARS, TrackPoint

MIN_RAW_POINTS = 50
MAX_RAW_POINTS = 80_000
MIN_STEP_M = 8.0
MAX_KEPT_POINTS = 25_000
GAP_WARN_M = 200.0
SNAP_MAX_M = 75.0
DUP_ALONG_M = 100.0
MAX_WAYPOINTS = 40
STEEP_M_PER_KM = 160.0
SMALL_GAP_POINTS = 8
SMALL_GAP_M = 50.0
KNOWN_ELE_FRACTION = 0.10
PREVIEW_POINTS = 64

NOT_XML = (
    "This file is not valid GPX. Choose a .gpx file exported from your watch or mapping app."
)
NO_COURSE = "This GPX has no track or route. Export the course as a track and try again."
TOO_BIG = "This file is too large. Keep it under 8 MB."
FLAT_ELE = (
    "This GPX has no usable elevation. The course is treated as flat, so climbs will not slow the plan."
)
PARTIAL_ELE = (
    "Elevation was missing on {count} points. Gaps up to 50 m were filled in; longer gaps were kept flat."
)
BAD_TRACK = "Choose one of the tracks in this file."

_EDGE = r"A-Za-z0-9А-Яа-яЁё"
_BEFORE = rf"(?<![{_EDGE}])"
_AFTER = rf"(?![{_EDGE}])"
_AS_NAME = re.compile(_BEFORE + r"as\s*\d*" + _AFTER, re.IGNORECASE)
_SHORT = re.compile(_BEFORE + r"(?:cp|пп)\s*\d*" + _AFTER, re.IGNORECASE)
_PP = re.compile(_BEFORE + r"пп\s*\d*" + _AFTER, re.IGNORECASE)
_WORD = re.compile(
    _BEFORE
    + r"(?:aid|checkpoint|control|water|h2o|drop|refreshment|refresh|feed(?:ing)?|food|hydration|подкрепителен|контрола|вода)"
    + _AFTER,
    re.IGNORECASE,
)
_FOOD = re.compile(
    _BEFORE + r"(?:aid|подкрепителен|refreshment|refresh|feed(?:ing)?|food)" + _AFTER,
    re.IGNORECASE,
)
_DROP = re.compile(_BEFORE + r"drop" + _AFTER, re.IGNORECASE)


def too_few_points(count: int) -> str:
    return f"This track has {count} points. A race file needs at least 50 points."


def too_many_points(count: int) -> str:
    return f"This track has {count} points. Keep the recording under 80,000 points."


@dataclass(frozen=True)
class UploadCourse:
    """An uploaded course ready to show and to plan from."""

    profile: list[TrackPoint]
    warnings: list[str]
    tracks: list[dict[str, object]]
    selected: int | str
    waypoints: list[dict[str, object]]
    distance_km: float
    elevation_gain_m: float
    elevation_loss_m: float
    highest_m: float
    profile_preview: list[dict[str, float]]
    distance_scale: float
    declared_km: float


def _points_of(path: RawPath) -> list[RawSample]:
    return [sample for segment in path.segments for sample in segment]


def _length_m(points: list[RawSample]) -> float:
    total = 0.0
    for left, right in zip(points, points[1:]):
        total += haversine_m(left.lat, left.lon, right.lat, right.lon)
    return total


def _read_document(xml_text: str):
    if xml_text is None or not str(xml_text).strip():
        raise ValueError(NOT_XML)
    if len(xml_text) > GPX_XML_MAX_CHARS:
        raise ValueError(TOO_BIG)
    try:
        return read_gpx(xml_text)
    except ValueError as exc:
        raise ValueError(NOT_XML) from None


def _choose(paths: tuple[RawPath, ...], track: str | int | None) -> tuple[list[RawPath], int | str]:
    mode = "longest" if track is None or track == "" else track
    if mode == "longest":
        index = max(range(len(paths)), key=lambda item: _length_m(_points_of(paths[item])))
        return [paths[index]], index
    if mode == "merge":
        return list(paths), "merge"
    try:
        index = int(mode)
    except (TypeError, ValueError) as exc:
        raise ValueError(BAD_TRACK) from None
    if index < 0 or index >= len(paths):
        raise ValueError(BAD_TRACK)
    return [paths[index]], index


def _repair_elevation(points: list[RawSample]) -> tuple[list[float], list[str]]:
    """Fill small elevation holes. A course with almost no heights stays flat."""

    known = [(index, point.elevation_m) for index, point in enumerate(points) if point.elevation_m is not None]
    count = len(points)
    if len(known) < 2 or len(known) / count < KNOWN_ELE_FRACTION:
        if known:
            values = sorted(value for _, value in known)
            flat = values[len(values) // 2]
        else:
            flat = 0.0
        return [flat] * count, [FLAT_ELE]

    elev: list[float | None] = [None] * count
    for index, value in known:
        elev[index] = value
    missing = 0
    cursor = 0
    while cursor < count:
        if elev[cursor] is not None:
            cursor += 1
            continue
        start = cursor
        while cursor < count and elev[cursor] is None:
            cursor += 1
        end = cursor
        missing += end - start
        left = start - 1
        right = end if end < count else None
        gap_points = end - start
        if left >= 0 and right is not None and gap_points <= SMALL_GAP_POINTS and _length_m(points[left:right + 1]) <= SMALL_GAP_M:
            span = _length_m(points[left:right + 1])
            for index in range(start, end):
                along = _length_m(points[left:index + 1])
                fraction = 0.0 if span <= 0 else along / span
                elev[index] = elev[left] + (elev[right] - elev[left]) * fraction
        elif left >= 0:
            for index in range(start, end):
                elev[index] = elev[left]
        else:
            for index in range(start, end):
                elev[index] = elev[right]
    warnings = [PARTIAL_ELE.format(count=missing)] if missing else []
    return [float(value) for value in elev], warnings


def _thin(points: list[TrackPoint], step_m: float) -> list[TrackPoint]:
    """Keep original vertices at least `step_m` apart, plus the finish."""

    if len(points) < 3:
        return list(points)
    kept = [points[0]]
    for point in points[1:-1]:
        previous = kept[-1]
        if haversine_m(previous.lat, previous.lon, point.lat, point.lon) >= step_m:
            kept.append(point)
    kept.append(points[-1])
    return kept


def _downsample(points: list[TrackPoint]) -> tuple[list[TrackPoint], str | None]:
    step = MIN_STEP_M
    thinned = _thin(points, step)
    while len(thinned) > MAX_KEPT_POINTS and step < 5_000:
        step *= 1.25
        thinned = _thin(points, step)
    if step > MIN_STEP_M + 1e-9:
        note = (
            f"Track was simplified to {len(thinned)} points, about {step:.0f} m apart, "
            "so the plan can run on this phone."
        )
        return thinned, note
    return thinned, None


def _step_notes(points: list[TrackPoint]) -> list[str]:
    jumps: list[str] = []
    spikes: list[str] = []
    distance = 0.0
    for left, right in zip(points, points[1:]):
        step = haversine_m(left.lat, left.lon, right.lat, right.lon)
        delta = right.elevation_m - left.elevation_m
        if step > GAP_WARN_M and len(jumps) < 3:
            jumps.append(f"The track jumps {step:.0f} m near {distance / 1000:.1f} km.")
        elif len(spikes) < 3 and (
            (step >= 5 and abs(delta) / step > 0.8) or (step < 15 and abs(delta) > 40)
        ):
            spikes.append(
                f"A step near {distance / 1000:.1f} km changes {abs(delta):.0f} m in {step:.0f} m. "
                "That looks like a GPS spike."
            )
        distance += step
    return jumps + spikes


def _steep_note(distance_km: float, gain_m: float, loss_m: float) -> list[str]:
    if distance_km < 1:
        return []
    notes = []
    if gain_m / distance_km > STEEP_M_PER_KM:
        notes.append(
            f"Elevation gain is {gain_m / distance_km:.0f} m per km. That is unusually steep, so check the file."
        )
    if loss_m / distance_km > STEEP_M_PER_KM:
        notes.append(
            f"Elevation loss is {loss_m / distance_km:.0f} m per km. That is unusually steep, so check the file."
        )
    return notes


def _waypoint_label(point: RawWaypoint) -> str:
    return (point.name or point.comment or "Aid").strip()[:80] or "Aid"


def _matched(point: RawWaypoint) -> dict[str, bool] | None:
    """Aid words, with `as` read only from the waypoint name."""

    name = point.name or ""
    other = " ".join(part for part in (point.comment, point.point_type, point.symbol) if part)
    blob = f"{name} {other}".strip()
    as_hit = _AS_NAME.search(name) is not None
    short_hit = _SHORT.search(blob) is not None
    word_hit = _WORD.search(blob) is not None
    if not as_hit and not short_hit and not word_hit:
        return None
    has_food = as_hit or _PP.search(blob) is not None or _FOOD.search(blob) is not None
    return {"has_food": has_food, "has_water": True, "drop_bag": _DROP.search(blob) is not None}


def _waypoints(points: tuple[RawWaypoint, ...], profile: list[TrackPoint]) -> tuple[list[dict[str, object]], list[str]]:
    course = prepare_course(profile)
    matched: list[tuple[RawWaypoint, dict[str, bool]]] = []
    for point in points:
        kind = _matched(point)
        if kind is not None:
            matched.append((point, kind))
    located = []
    for point, kind in matched:
        snapped, off_m, _index = snap_to_track(course, point.lat, point.lon)
        located.append((snapped.distance_m, off_m, point, kind, snapped))
    located.sort(key=lambda item: item[0])
    kept_m: list[float] = []
    rows: list[dict[str, object]] = []
    warnings: list[str] = []
    for distance_m, off_m, point, kind, snapped in located:
        label = _waypoint_label(point)
        if off_m > SNAP_MAX_M:
            warnings.append(f"{label} is {off_m:.0f} m off the track, so it was left off the aid list.")
            continue
        if any(abs(distance_m - previous) <= DUP_ALONG_M for previous in kept_m):
            warnings.append(f"{label} is within 100 m of another aid, so it was left off the aid list.")
            continue
        if len(rows) >= MAX_WAYPOINTS:
            warnings.append(f"{label} was left off because the aid list holds 40 stations.")
            continue
        kept_m.append(distance_m)
        rows.append({
            "name": label,
            "distance_km": round(snapped.distance_m / 1000.0, 3),
            "lat": snapped.lat,
            "lon": snapped.lon,
            "elevation_m": round(snapped.elevation_m, 1),
            "has_food": kind["has_food"],
            "has_water": kind["has_water"],
            "drop_bag": kind["drop_bag"],
        })
    return rows, warnings


def _preview(profile: list[TrackPoint]) -> list[dict[str, float]]:
    course = prepare_course(profile)
    last = course[-1].distance_m
    preview: list[dict[str, float]] = []
    steps = PREVIEW_POINTS - 1
    for index in range(PREVIEW_POINTS):
        point = location_at_distance(course, last * index / steps)
        preview.append({"km": round(point.distance_m / 1000.0, 3), "ele": round(point.elevation_m, 1)})
    return preview


def _describe(paths: tuple[RawPath, ...]) -> list[dict[str, object]]:
    rows = []
    for index, path in enumerate(paths):
        points = _points_of(path)
        rows.append({
            "index": index,
            "name": path.name,
            "kind": path.kind,
            "points": len(points),
            "distance_km": round(_length_m(points) / 1000.0, 2),
        })
    return rows


def build_upload(
    xml_text: str,
    track: str | int | None = "longest",
    distance_override_km: float | None = None,
) -> UploadCourse:
    """Turn an upload into a course, a sanity report, and aid waypoints."""

    document = _read_document(xml_text)
    pool = document.tracks or document.routes
    if not pool or not any(_points_of(path) for path in pool):
        raise ValueError(NO_COURSE)
    chosen, selected = _choose(pool, track)
    raw = [sample for path in chosen for sample in _points_of(path)]
    if len(raw) < 2:
        raise ValueError(NO_COURSE)
    if len(raw) < MIN_RAW_POINTS:
        raise ValueError(too_few_points(len(raw)))
    if len(raw) > MAX_RAW_POINTS:
        raise ValueError(too_many_points(len(raw)))

    elevations, warnings = _repair_elevation(raw)
    repaired = [
        TrackPoint(lat=sample.lat, lon=sample.lon, elevation_m=elevations[index])
        for index, sample in enumerate(raw)
    ]
    warnings.extend(_step_notes(repaired))
    profile, thin_note = _downsample(repaired)
    if thin_note:
        warnings.append(thin_note)
    course = prepare_course(profile)
    segments = slice_segments(course)
    gain_m = sum(segment.elevation_gain_m for segment in segments)
    loss_m = sum(segment.elevation_loss_m for segment in segments)
    distance_km = course[-1].distance_m / 1000.0
    warnings.extend(_steep_note(distance_km, gain_m, loss_m))
    stations, station_notes = _waypoints(document.waypoints, profile)
    warnings.extend(station_notes)
    scale = 1.0
    declared = 0.0
    if distance_override_km is not None and distance_override_km > 0 and distance_km > 0:
        scale = distance_override_km / distance_km
        declared = float(distance_override_km)
    return UploadCourse(
        profile=profile,
        warnings=warnings,
        tracks=_describe(pool),
        selected=selected,
        waypoints=stations,
        distance_km=distance_km,
        elevation_gain_m=gain_m,
        elevation_loss_m=loss_m,
        highest_m=max(point.elevation_m for point in course),
        profile_preview=_preview(profile),
        distance_scale=scale,
        declared_km=declared,
    )


def inspect_upload(xml_text: str, track: str | int | None = "longest") -> dict[str, object]:
    """Sanity report for the setup card. No fuel plan is built."""

    ready = build_upload(xml_text, track=track)
    return {
        "distance_km": round(ready.distance_km, 2),
        "elevation_gain_m": round(ready.elevation_gain_m, 0),
        "elevation_loss_m": round(ready.elevation_loss_m, 0),
        "highest_m": round(ready.highest_m, 0),
        "profile": ready.profile_preview,
        "warnings": ready.warnings,
        "tracks": ready.tracks,
        "selected": ready.selected,
        "waypoints": ready.waypoints,
    }
