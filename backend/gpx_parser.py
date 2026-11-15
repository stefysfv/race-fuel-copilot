"""Turn a raw track into smoothed points and 500 m course segments.

Accepts a GPX XML string or a `.gpx` file path from `races.json`. Distance
uses the Haversine formula. Elevation is smoothed with a 5-point moving
average before the track is sliced, which removes the small up-and-down
noise typical of barometric GPS.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import gpxpy

from backend.models import Race, TrackPoint

# `gpx_parser.py` lives in `backend/`, so parents[1] is the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

EARTH_RADIUS_M = 6_371_000.0
SEGMENT_LENGTH_M = 500.0
SMOOTHING_WINDOW = 5
# A leftover shorter than this is folded into the previous slice so the last
# segment cannot be a few metres of steep GPS noise.
MIN_TAIL_M = 100.0


@dataclass(frozen=True)
class CoursePoint:
    """One track sample after elevation smoothing, with distance from the start."""

    lat: float
    lon: float
    elevation_m: float
    distance_m: float


@dataclass(frozen=True)
class CourseSegment:
    """Geometry for one slice. Pace and digestive zone are added later."""

    index: int
    start_distance_m: float
    end_distance_m: float
    distance_m: float
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    elevation_start_m: float
    elevation_end_m: float
    elevation_gain_m: float
    elevation_loss_m: float
    grade_percent: float


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres between two WGS84 points.

    a = sin^2(dlat/2) + cos(lat1) * cos(lat2) * sin^2(dlon/2)
    distance = 2 * R * atan2(sqrt(a), sqrt(1-a))
    """

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    a = min(1.0, max(0.0, a))
    arc = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * arc


def smooth_elevation(values: list[float], window: int = SMOOTHING_WINDOW) -> list[float]:
    """5-point moving average.

    The window shrinks at the ends of the track. Padding with invented
    elevations would hide a real climb that starts at the trailhead.
    """

    if window < 1 or window % 2 == 0:
        raise ValueError("Smoothing window must be a positive odd integer")
    if not values:
        return []
    half = window // 2
    smoothed: list[float] = []
    last = len(values)
    for index in range(last):
        start = max(0, index - half)
        end = min(last, index + half + 1)
        chunk = values[start:end]
        smoothed.append(sum(chunk) / len(chunk))
    return smoothed


def resolve_gpx_path(gpx_file: str) -> Path:
    """Resolve a catalog path like `backend/data/gpx/salomon-vitosha-27.gpx`."""

    path = Path(gpx_file)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_gpx_file(gpx_file: str) -> list[TrackPoint]:
    """Read every track point from a GPX file on disk."""

    path = resolve_gpx_path(gpx_file)
    if not path.is_file():
        raise ValueError(f"GPX file not found: {gpx_file}")
    return parse_gpx_xml(path.read_text(encoding="utf-8"))


def attach_track(race: Race) -> Race:
    """Return the race with `profile` filled from its GPX file.

    Catalog races store a path instead of thousands of coordinates. Slicing
    still happens later, in `prepare_course` and `slice_segments`.
    """

    if len(race.profile) >= 2 or not race.gpx_file:
        return race
    return race.model_copy(update={"profile": load_gpx_file(race.gpx_file)})


def parse_gpx_xml(xml_text: str) -> list[TrackPoint]:
    """Read track points from a GPX document. Routes are used if there is no track."""

    try:
        gpx = gpxpy.parse(xml_text)
    except Exception as exc:
        raise ValueError(f"Could not parse GPX: {exc}") from exc

    raw_points = [point for track in gpx.tracks for segment in track.segments for point in segment.points]
    if not raw_points:
        raw_points = [point for route in gpx.routes for point in route.points]
    if len(raw_points) < 2:
        raise ValueError("GPX must contain at least two track points")

    return [
        TrackPoint(
            lat=point.latitude,
            lon=point.longitude,
            elevation_m=0.0 if point.elevation is None else float(point.elevation),
        )
        for point in raw_points
    ]


def prepare_course(raw_points: list[TrackPoint]) -> list[CoursePoint]:
    """Smooth elevation and attach a cumulative Haversine distance to each point."""

    if len(raw_points) < 2:
        raise ValueError("A course needs at least two track points")

    elevations = smooth_elevation([point.elevation_m for point in raw_points])
    prepared: list[CoursePoint] = []
    distance_m = 0.0
    for index, point in enumerate(raw_points):
        if index > 0:
            previous = raw_points[index - 1]
            distance_m += haversine_m(previous.lat, previous.lon, point.lat, point.lon)
        prepared.append(
            CoursePoint(
                lat=point.lat,
                lon=point.lon,
                elevation_m=elevations[index],
                distance_m=distance_m,
            )
        )
    if prepared[-1].distance_m <= 0:
        raise ValueError("Course length is zero: every track point has the same coordinates")
    return prepared


def location_at_distance(points: list[CoursePoint], distance_m: float) -> CoursePoint:
    """Interpolate lat, lon, and elevation at a distance along the course."""

    if not points:
        raise ValueError("Course is empty")
    distance_m = min(max(distance_m, 0.0), points[-1].distance_m)
    for index in range(len(points) - 1):
        left = points[index]
        right = points[index + 1]
        if distance_m > right.distance_m and index < len(points) - 2:
            continue
        span = right.distance_m - left.distance_m
        fraction = 0.0 if span <= 0 else (distance_m - left.distance_m) / span
        fraction = min(max(fraction, 0.0), 1.0)
        return CoursePoint(
            lat=left.lat + (right.lat - left.lat) * fraction,
            lon=left.lon + (right.lon - left.lon) * fraction,
            elevation_m=left.elevation_m + (right.elevation_m - left.elevation_m) * fraction,
            distance_m=distance_m,
        )
    return points[-1]


def slice_segments(
    points: list[CoursePoint],
    segment_length_m: float = SEGMENT_LENGTH_M,
) -> list[CourseSegment]:
    """Cut the course into consecutive slices of about 500 m.

    The final slice keeps a short tail (under 100 m) instead of becoming its
    own segment. Grade is the net elevation change over the slice, which is
    what the pacing model uses. Gain and loss still count every up and down
    inside the slice.
    """

    if segment_length_m <= 0:
        raise ValueError("Segment length must be positive")
    if len(points) < 2:
        raise ValueError("Need at least two course points to slice")

    total_m = points[-1].distance_m
    cuts = [0.0]
    cursor = segment_length_m
    while cursor < total_m - MIN_TAIL_M:
        cuts.append(cursor)
        cursor += segment_length_m
    cuts.append(total_m)

    segments: list[CourseSegment] = []
    for index in range(len(cuts) - 1):
        segments.append(_build_segment(points, index, cuts[index], cuts[index + 1]))
    return segments


def _build_segment(
    points: list[CoursePoint],
    index: int,
    start_m: float,
    end_m: float,
) -> CourseSegment:
    start = location_at_distance(points, start_m)
    end = location_at_distance(points, end_m)
    gain_m, loss_m = _gain_loss(points, start_m, end_m)
    distance_m = end_m - start_m
    grade_percent = 0.0 if distance_m <= 0 else ((end.elevation_m - start.elevation_m) / distance_m) * 100.0
    return CourseSegment(
        index=index,
        start_distance_m=start_m,
        end_distance_m=end_m,
        distance_m=distance_m,
        start_lat=start.lat,
        start_lon=start.lon,
        end_lat=end.lat,
        end_lon=end.lon,
        elevation_start_m=start.elevation_m,
        elevation_end_m=end.elevation_m,
        elevation_gain_m=gain_m,
        elevation_loss_m=loss_m,
        grade_percent=grade_percent,
    )


def _gain_loss(points: list[CoursePoint], start_m: float, end_m: float) -> tuple[float, float]:
    elevations = [location_at_distance(points, start_m).elevation_m]
    for point in points:
        if start_m < point.distance_m < end_m:
            elevations.append(point.elevation_m)
    elevations.append(location_at_distance(points, end_m).elevation_m)

    gain_m = 0.0
    loss_m = 0.0
    for before, after in zip(elevations, elevations[1:]):
        delta = after - before
        if delta >= 0:
            gain_m += delta
        else:
            loss_m -= delta
    return gain_m, loss_m
