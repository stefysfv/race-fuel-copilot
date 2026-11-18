"""Turn a raw track into smoothed points and 500 m course segments.

Accepts GPX text. Distance uses the Haversine formula. Elevation is smoothed
with a 5-point moving average before the track is sliced, which removes the
small up-and-down noise typical of barometric GPS. Reading a GPX file from
disk lives in `backend.adapters`.
"""

from __future__ import annotations

import bisect
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from backend.models import TrackPoint

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


def _local_name(tag: str) -> str:
    """Drop an XML namespace so GPX 1.0 and 1.1 tags match."""

    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _elevation_m(element: ET.Element) -> float:
    """Read `<ele>`. A missing or blank elevation is 0 m, matching the old parser."""

    for child in list(element):
        if _local_name(child.tag) != "ele":
            continue
        text = (child.text or "").strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError as exc:
            raise ValueError(f"Could not parse GPX: {exc}") from exc
    return 0.0


def _points_named(root: ET.Element, name: str) -> list[TrackPoint]:
    """Collect `trkpt` or `rtept` elements in document order."""

    points: list[TrackPoint] = []
    for element in root.iter():
        if _local_name(element.tag) != name:
            continue
        lat_text = element.get("lat")
        lon_text = element.get("lon")
        if lat_text is None or lon_text is None:
            continue
        try:
            lat = float(lat_text)
            lon = float(lon_text)
        except ValueError as exc:
            raise ValueError(f"Could not parse GPX: {exc}") from exc
        points.append(TrackPoint(lat=lat, lon=lon, elevation_m=_elevation_m(element)))
    return points


def parse_gpx_xml(xml_text: str) -> list[TrackPoint]:
    """Read track points from a GPX document. Routes are used if there is no track.

    GPX 1.0 (`http://www.topografix.com/GPX/1/0`) and GPX 1.1
    (`http://www.topografix.com/GPX/1/1`) are both accepted, with or without
    a default namespace. `<trkpt>` wins. `<rtept>` is used only when the
    document has no track points.
    """

    if not xml_text or not str(xml_text).strip():
        raise ValueError("Could not parse GPX: document is empty")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Could not parse GPX: {exc}") from exc

    raw_points = _points_named(root, "trkpt")
    if not raw_points:
        raw_points = _points_named(root, "rtept")
    if len(raw_points) < 2:
        raise ValueError("GPX must contain at least two track points")
    return raw_points


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
    """Interpolate lat, lon, and elevation at a distance along the course.

    Distances only increase, so the span is found with a binary search.
    """

    if not points:
        raise ValueError("Course is empty")
    last = len(points) - 1
    distance_m = min(max(distance_m, 0.0), points[last].distance_m)
    if last == 0:
        return points[0]
    left, right = _span_at(points, distance_m)
    span = right.distance_m - left.distance_m
    fraction = 0.0 if span <= 0 else (distance_m - left.distance_m) / span
    fraction = min(max(fraction, 0.0), 1.0)
    return CoursePoint(
        lat=left.lat + (right.lat - left.lat) * fraction,
        lon=left.lon + (right.lon - left.lon) * fraction,
        elevation_m=left.elevation_m + (right.elevation_m - left.elevation_m) * fraction,
        distance_m=distance_m,
    )


def _span_at(points: list[CoursePoint], distance_m: float) -> tuple[CoursePoint, CoursePoint]:
    """Return the two samples that bracket `distance_m`.

    `distance_m` is already clamped to the course. Equal distances keep the
    first span that reaches the query, matching a front-to-back scan.
    """

    last = len(points) - 1
    index = bisect.bisect_left(points, distance_m, key=lambda point: point.distance_m)
    left_index = 0 if index <= 0 else (last - 1 if index > last else index - 1)
    return points[left_index], points[left_index + 1]


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


# Running gain and loss up to each sample. Keyed by the point list so each
# slice reads two entries instead of walking the whole track again.
_ELEVATION_PREFIX: dict[int, tuple[list[CoursePoint], list[float], list[float]]] = {}


def _elevation_prefix(points: list[CoursePoint]) -> tuple[list[float], list[float]]:
    """Prefix sums of positive and negative elevation steps between samples."""

    cached = _ELEVATION_PREFIX.get(id(points))
    if cached is not None and cached[0] is points:
        return cached[1], cached[2]
    gain_prefix = [0.0]
    loss_prefix = [0.0]
    gain_m = 0.0
    loss_m = 0.0
    previous = points[0].elevation_m
    for point in points[1:]:
        delta = point.elevation_m - previous
        if delta >= 0:
            gain_m += delta
        else:
            loss_m -= delta
        gain_prefix.append(gain_m)
        loss_prefix.append(loss_m)
        previous = point.elevation_m
    if len(_ELEVATION_PREFIX) > 4:
        _ELEVATION_PREFIX.clear()
    _ELEVATION_PREFIX[id(points)] = (points, gain_prefix, loss_prefix)
    return gain_prefix, loss_prefix


def _add_step(gain_m: float, loss_m: float, delta: float) -> tuple[float, float]:
    if delta >= 0:
        return gain_m + delta, loss_m
    return gain_m, loss_m - delta


def _gain_loss(points: list[CoursePoint], start_m: float, end_m: float) -> tuple[float, float]:
    """Elevation gained and lost between two distances.

    Samples strictly inside the slice come from the prefix sums. The slice
    still starts and ends on the interpolated elevations.
    """

    start_elevation = location_at_distance(points, start_m).elevation_m
    end_elevation = location_at_distance(points, end_m).elevation_m
    first_inside = bisect.bisect_right(points, start_m, key=lambda point: point.distance_m)
    end_inside = bisect.bisect_left(points, end_m, key=lambda point: point.distance_m)
    if first_inside >= end_inside:
        return _add_step(0.0, 0.0, end_elevation - start_elevation)

    gain_prefix, loss_prefix = _elevation_prefix(points)
    gain_m, loss_m = _add_step(0.0, 0.0, points[first_inside].elevation_m - start_elevation)
    if end_inside - 1 > first_inside:
        gain_m += gain_prefix[end_inside - 1] - gain_prefix[first_inside]
        loss_m += loss_prefix[end_inside - 1] - loss_prefix[first_inside]
    return _add_step(gain_m, loss_m, end_elevation - points[end_inside - 1].elevation_m)
