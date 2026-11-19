"""Compare recorded moving time with the engine's Minetti relative cost.

The engine constants are only read. Nothing here writes back to them.

For every GPX whose track points carry <time>:

1. Slice the track with the engine's own prepare_course and slice_segments.
2. Drop time that is not running: slower than 1 km/h, an isolated GPS jump,
   a zero-length timestamp, or a linger within 20 m of along-track progress
   inside an aid-station radius. A recording with long stretches at or above
   25 km/h stays in the file list but out of the pooled running comparison.
3. Anchor one flat pace per recording on slices whose net grade is between
   -5% and +5%. Predicted minutes are moving kilometres times that pace
   times gap_cost_factor(grade).
4. Bucket the slices by net grade in 10-point windows (-30..-20, -20..-10,
   and so on). A bucket is unreliable when it has fewer than 4 slices, under
   1 km of moving distance, or under 3 minutes of moving time.

Writes a CSV, a text table, and a PNG chart. The chart needs matplotlib,
which is a dev tool and is not listed in requirements.txt.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.engine.constants import FLAT_COST_J_PER_KG_M, MIN_COST_J_PER_KG_M
from backend.engine.gap import gap_cost_factor, metabolic_cost_j_per_kg_m
from backend.gpx_parser import parse_gpx_xml, prepare_course, slice_segments
from backend.models import TrackPoint

# Moving time keeps a step at or above this speed. Anything slower is a stop.
STOP_SPEED_KMH = 1.0
# 25 km/h is 2:24 per km. A trail run does not hold that for hundreds of metres
# on flat ground. A short blip at 30 km/h is treated as a GPS jump instead.
FAST_SPEED_KMH = 25.0
SUSTAINED_FAST_M = 200.0
NON_RUNNING_FAST_M = 500.0
SPIKE_SPEED_KMH = 30.0
SPIKE_RUN_MAX_M = 80.0
# Aid linger: inside this radius of a catalogued station, a stretch that
# stays within 20 m of along-track progress for at least 45 s, and never
# steps faster than a shuffle, is a pause rather than hiking into the aid.
AID_RADIUS_M = 150.0
AID_BAND_M = 20.0
AID_LINGER_S = 45.0
AID_SHUFFLE_KMH = 4.0
# Flat pace is this recording's own moving pace on near-flat slices.
FLAT_ANCHOR_LO = -5.0
FLAT_ANCHOR_HI = 5.0
FLAT_ANCHOR_MIN_M = 1_000.0
BUCKET_WIDTH = 10.0
# A grade window with less evidence than this is marked unreliable.
MIN_SLICES = 4
MIN_MOVING_M = 1_000.0
MIN_MOVING_S = 180.0
CATALOG_PATH = ROOT / "frontend" / "data" / "races.json"
DEFAULT_OUT = ROOT / "docs" / "calibration"


@dataclass
class TimedPoint:
    """One track point, in the same order the engine parser returns."""

    lat: float
    lon: float
    elevation_m: float
    when: datetime


@dataclass
class Interval:
    """One step between consecutive course points."""

    d0_m: float
    d1_m: float
    dt_s: float
    speed_kmh: float
    kind: str = "moving"

    @property
    def distance_m(self) -> float:
        return self.d1_m - self.d0_m

    @property
    def mid_m(self) -> float:
        return (self.d0_m + self.d1_m) / 2.0


@dataclass
class Activity:
    """One unique timestamped recording, after the engine has sliced it."""

    name: str
    source: Path
    copies: list[Path]
    started: datetime
    finished: datetime
    course_m: float
    slices: list[MeasuredSlice]
    moving_s: float
    stopped_s: float
    aid_s: float
    glitch_s: float
    glitch_m: float
    spike_m: float
    fast_m: float
    fast_s: float
    fast_grades: tuple[tuple[float, float, float], ...]
    zero_dt_steps: int
    speed_p50: float
    speed_p90: float
    speed_max: float
    in_pool: bool
    flat_pace_min_per_km: float
    anchor_m: float
    anchor_window: str
    clamped_slices: int


@dataclass
class MeasuredSlice:
    """Moving time on one engine slice, tagged with that slice's net grade."""

    activity: str
    segment_index: int
    grade_percent: float
    moving_m: float
    moving_s: float
    flat_pace_min_per_km: float

    @property
    def minetti_factor(self) -> float:
        return gap_cost_factor(self.grade_percent)

    def predicted_s(self, factor: float | None = None) -> float:
        """Moving-time prediction at this recording's flat pace."""

        used = self.minetti_factor if factor is None else factor
        kilometres = self.moving_m / 1000.0
        return kilometres * self.flat_pace_min_per_km * used * 60.0


@dataclass
class Bucket:
    """Distance-weighted totals for one grade window."""

    activity: str
    lo: float
    hi: float
    slices: int = 0
    moving_m: float = 0.0
    moving_s: float = 0.0
    predicted_s: float = 0.0
    flat_s: float = 0.0
    grade_distance_m: float = 0.0
    factor_distance: float = 0.0
    in_running_pool: bool = True

    @property
    def label(self) -> str:
        return _bucket_label(self.lo, self.hi)

    @property
    def actual_min(self) -> float:
        return self.moving_s / 60.0

    @property
    def predicted_min(self) -> float:
        return self.predicted_s / 60.0

    @property
    def error_min(self) -> float:
        return self.predicted_min - self.actual_min

    @property
    def error_pct(self) -> float | None:
        if self.actual_min <= 0:
            return None
        return 100.0 * self.error_min / self.actual_min

    @property
    def actual_factor(self) -> float | None:
        """Recorded minutes divided by flat-pace minutes. 1.0 means flat pace."""

        if self.flat_s <= 0:
            return None
        return self.moving_s / self.flat_s

    @property
    def minetti_factor(self) -> float | None:
        if self.moving_m <= 0:
            return None
        return self.factor_distance / self.moving_m

    @property
    def mean_grade(self) -> float | None:
        if self.moving_m <= 0:
            return None
        return self.grade_distance_m / self.moving_m

    @property
    def unreliable_reason(self) -> str:
        reasons: list[str] = []
        if self.slices < MIN_SLICES:
            reasons.append(f"{self.slices} slices < {MIN_SLICES}")
        if self.moving_m < MIN_MOVING_M:
            reasons.append(f"{self.moving_m / 1000.0:.2f} km < 1 km")
        if self.moving_s < MIN_MOVING_S:
            reasons.append(f"{self.moving_s / 60.0:.1f} min < 3 min")
        return "; ".join(reasons)


def _local_name(tag: str) -> str:
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag


def _parse_time(text: str) -> datetime:
    cleaned = text.strip()
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    return datetime.fromisoformat(cleaned)


def _bucket_label(lo: float, hi: float) -> str:
    """Windows read -20..-10 and +10..+20, matching the calibration brief."""

    return f"{_signed(lo)}..{_signed(hi)}"


def _signed(grade: float) -> str:
    if grade > 0:
        return f"+{grade:.0f}"
    return f"{grade:.0f}"


def _bucket_lo(grade: float) -> float:
    """Left edge of the 10-point window that contains this grade.

    -30% lands in -30..-20. 0% lands in 0..+10. The right edge is exclusive
    except that a grade sitting on the right edge belongs to the next window.
    """

    return math.floor(grade / BUCKET_WIDTH) * BUCKET_WIDTH


def find_gpx_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.gpx") if path.is_file())


def choose_unique(paths: list[Path]) -> list[tuple[Path, list[Path]]]:
    """One path per file hash. Prefer the catalog copy when the bytes match."""

    groups: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        groups[digest].append(path)
    chosen: list[tuple[Path, list[Path]]] = []
    for copies in groups.values():
        copies.sort(key=lambda path: (0 if _is_catalog_gpx(path) else 1, str(path)))
        chosen.append((copies[0], copies[1:]))
    chosen.sort(key=lambda item: item[0].name)
    return chosen


def _is_catalog_gpx(path: Path) -> bool:
    parts = path.parts
    return "data" in parts and "gpx" in parts


def load_aid_distances(stem: str) -> list[float]:
    """Catalogued aid stations for this file, as metres along the recording."""

    if not CATALOG_PATH.is_file():
        return []
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    for race in catalog.get("races", []):
        gpx_name = str(race.get("gpx_file", ""))
        if race.get("id") == stem or gpx_name.endswith(stem + ".gpx"):
            return [float(station["distance_km"]) * 1000.0 for station in race.get("aid_stations", [])]
    return []


def parse_timed_points(path: Path) -> list[TimedPoint]:
    """Read lat, lon, elevation, and time in track-point order.

    The engine parser drops time. Zipping this list with parse_gpx_xml is
    safe only when every track point has the same coordinates in both reads.
    """

    root = ET.parse(path).getroot()
    points: list[TimedPoint] = []
    for element in root.iter():
        if _local_name(element.tag) != "trkpt":
            continue
        lat_text = element.get("lat")
        lon_text = element.get("lon")
        if lat_text is None or lon_text is None:
            continue
        when: datetime | None = None
        elevation_m = 0.0
        for child in list(element):
            name = _local_name(child.tag)
            text = (child.text or "").strip()
            if name == "time" and text:
                when = _parse_time(text)
            elif name == "ele" and text:
                elevation_m = float(text)
        if when is None:
            continue
        points.append(
            TimedPoint(
                lat=float(lat_text),
                lon=float(lon_text),
                elevation_m=elevation_m,
                when=when,
            )
        )
    return points


def _same_track(engine_points: list[TrackPoint], timed: list[TimedPoint]) -> bool:
    if len(engine_points) != len(timed):
        return False
    for engine_point, timed_point in zip(engine_points, timed, strict=True):
        if engine_point.lat != timed_point.lat or engine_point.lon != timed_point.lon:
            return False
    return True


def build_intervals(distances_m: list[float], times: list[datetime]) -> list[Interval]:
    """One interval per step. Speed uses the engine's own cumulative distance."""

    intervals: list[Interval] = []
    for index in range(1, len(distances_m)):
        distance_m = distances_m[index] - distances_m[index - 1]
        dt_s = (times[index] - times[index - 1]).total_seconds()
        if dt_s <= 0 or distance_m < 0:
            speed_kmh = 0.0
            kind = "glitch"
        else:
            speed_kmh = (distance_m / dt_s) * 3.6
            if speed_kmh < STOP_SPEED_KMH:
                kind = "stopped"
            else:
                kind = "moving"
        intervals.append(
            Interval(
                d0_m=distances_m[index - 1],
                d1_m=distances_m[index],
                dt_s=max(dt_s, 0.0),
                speed_kmh=speed_kmh,
                kind=kind,
            )
        )
    return intervals


def mark_fast_and_spikes(intervals: list[Interval]) -> None:
    """Separate a bike-or-vehicle stretch from a one-step GPS jump.

    Contiguous steps at or above 25 km/h covering at least 200 m are marked
    `fast`. A shorter burst whose fastest step is at least 30 km/h, and which
    covers at most 80 m, is marked `spike`. A brief descent quicker than
    25 km/h but shorter than those tests stays moving.
    """

    index = 0
    count = len(intervals)
    while index < count:
        if intervals[index].dt_s <= 0 or intervals[index].speed_kmh < FAST_SPEED_KMH:
            index += 1
            continue
        end = index
        while end < count and intervals[end].dt_s > 0 and intervals[end].speed_kmh >= FAST_SPEED_KMH:
            end += 1
        distance_m = sum(intervals[cursor].distance_m for cursor in range(index, end))
        peak_kmh = max(intervals[cursor].speed_kmh for cursor in range(index, end))
        if distance_m >= SUSTAINED_FAST_M:
            for cursor in range(index, end):
                intervals[cursor].kind = "fast"
        elif peak_kmh >= SPIKE_SPEED_KMH and distance_m <= SPIKE_RUN_MAX_M:
            for cursor in range(index, end):
                if intervals[cursor].speed_kmh >= SPIKE_SPEED_KMH:
                    intervals[cursor].kind = "spike"
        index = end


def mark_aid_dwells(intervals: list[Interval], aid_distances_m: list[float]) -> None:
    """Mark shuffles that stay put beside a catalogued aid station.

    Hiking into the station keeps its time. A stretch counts only when every
    step is under a shuffle and the along-track position stays inside a 20 m
    band for at least 45 seconds. Those steps are aid pauses even if a few of
    them creep faster than the 1 km/h stop rule.
    """

    if not aid_distances_m:
        return
    near = [
        any(abs(interval.mid_m - aid_m) <= AID_RADIUS_M for aid_m in aid_distances_m)
        for interval in intervals
    ]
    start = 0
    while start < len(intervals):
        if not near[start]:
            start += 1
            continue
        end = start
        while end < len(intervals) and near[end]:
            end += 1
        _mark_dwells_in_run(intervals, start, end)
        start = end


def _mark_dwells_in_run(intervals: list[Interval], start: int, end: int) -> None:
        for left in range(start, end):
            if intervals[left].kind in {"glitch", "spike", "fast"}:
                continue
            if intervals[left].speed_kmh >= AID_SHUFFLE_KMH:
                continue
            elapsed_s = 0.0
            low_m = intervals[left].d0_m
            high_m = intervals[left].d1_m
            right = left
            while right < end:
                interval = intervals[right]
                # A repeated timestamp is not motion. A spike, or a step that
                # leaves the 20 m band, ends the linger.
                if interval.kind in {"spike", "fast"}:
                    break
                if interval.kind != "glitch":
                    if interval.speed_kmh >= AID_SHUFFLE_KMH:
                        break
                    low_m = min(low_m, interval.d0_m, interval.d1_m)
                    high_m = max(high_m, interval.d0_m, interval.d1_m)
                    if high_m - low_m > AID_BAND_M:
                        break
                    elapsed_s += interval.dt_s
                if elapsed_s >= AID_LINGER_S:
                    for index in range(left, right + 1):
                        if intervals[index].kind in {"moving", "stopped"}:
                            intervals[index].kind = "aid"
                    break
                right += 1


def _assign_slices(
    intervals: list[Interval],
    segments: list,
) -> dict[int, dict[str, float]]:
    """Split every step across the engine slices it touches.

    A one-second GPS step rarely crosses a 500 m boundary. Splitting by
    distance still keeps the slice lengths exact.
    """

    totals: dict[int, dict[str, float]] = {
        segment.index: {"moving_m": 0.0, "moving_s": 0.0, "check_m": 0.0}
        for segment in segments
    }
    for interval in intervals:
        pieces = _split_interval(interval, segments)
        piece_distance = sum(distance_m for _index, distance_m in pieces)
        if interval.distance_m > 0 and abs(piece_distance - interval.distance_m) > 0.05:
            raise RuntimeError("a GPS step was not fully covered by the engine slices")
        for index, distance_m in pieces:
            bucket = totals[index]
            bucket["check_m"] += distance_m
            if interval.kind != "moving" or interval.distance_m <= 0:
                continue
            share = distance_m / interval.distance_m
            bucket["moving_m"] += distance_m
            bucket["moving_s"] += interval.dt_s * share
    for segment in segments:
        if abs(totals[segment.index]["check_m"] - segment.distance_m) > 0.05:
            raise RuntimeError(
                f"slice {segment.index} distance {totals[segment.index]['check_m']:.2f} "
                f"does not match the engine's {segment.distance_m:.2f}"
            )
    return totals


def _split_interval(interval: Interval, segments: list) -> list[tuple[int, float]]:
    if interval.distance_m <= 0:
        home = _segment_index_at(segments, interval.mid_m)
        return [(home, 0.0)]
    pieces: list[tuple[int, float]] = []
    for segment in segments:
        if segment.end_distance_m <= interval.d0_m:
            continue
        if segment.start_distance_m >= interval.d1_m:
            break
        left = max(interval.d0_m, segment.start_distance_m)
        right = min(interval.d1_m, segment.end_distance_m)
        if right > left:
            pieces.append((segment.index, right - left))
    return pieces


def _segment_index_at(segments: list, distance_m: float) -> int:
    for segment in segments:
        if distance_m < segment.end_distance_m:
            return segment.index
    return segments[-1].index


def _anchor_pace(grades_and_motion: list[tuple[float, float, float]]) -> tuple[float, float, str]:
    """Flat min/km from near-flat moving time. Widen the window if it is thin."""

    windows = ((-5.0, 5.0), (-8.0, 8.0), (-10.0, 10.0))
    for low, high in windows:
        distance_m = 0.0
        seconds = 0.0
        for grade, moving_m, moving_s in grades_and_motion:
            if low <= grade <= high and moving_m > 0:
                distance_m += moving_m
                seconds += moving_s
        if distance_m >= FLAT_ANCHOR_MIN_M and seconds > 0:
            pace = (seconds / 60.0) / (distance_m / 1000.0)
            label = f"{_signed(low)}..{_signed(high)}" if low != -5 else "-5..+5"
            if low < 0 < high and low == -5:
                label = "-5..+5"
            return pace, distance_m, label
    raise ValueError("not enough near-flat moving distance to anchor a flat pace")


def measure_activity(path: Path, copies: list[Path]) -> Activity | None:
    """Slice one recording. None means the track has no usable timestamps."""

    xml_text = path.read_text(encoding="utf-8")
    engine_points = parse_gpx_xml(xml_text)
    timed = parse_timed_points(path)
    if not timed:
        return None
    if not _same_track(engine_points, timed):
        raise RuntimeError(f"{path.name}: timestamps do not line up with the engine's track points")

    course = prepare_course(engine_points)
    segments = slice_segments(course)
    intervals = build_intervals(
        [point.distance_m for point in course],
        [point.when for point in timed],
    )
    mark_fast_and_spikes(intervals)
    mark_aid_dwells(intervals, load_aid_distances(path.stem))
    assigned = _assign_slices(intervals, segments)

    motion = [
        (segment.grade_percent, assigned[segment.index]["moving_m"], assigned[segment.index]["moving_s"])
        for segment in segments
    ]
    flat_pace, anchor_m, anchor_window = _anchor_pace(motion)

    slices: list[MeasuredSlice] = []
    clamped = 0
    for segment in segments:
        moving_m = assigned[segment.index]["moving_m"]
        moving_s = assigned[segment.index]["moving_s"]
        if abs(segment.grade_percent) > 40.0:
            clamped += 1
        if moving_m <= 0 or moving_s <= 0:
            continue
        slices.append(
            MeasuredSlice(
                activity=path.stem,
                segment_index=segment.index,
                grade_percent=segment.grade_percent,
                moving_m=moving_m,
                moving_s=moving_s,
                flat_pace_min_per_km=flat_pace,
            )
        )

    def _seconds(kind: str) -> float:
        return sum(interval.dt_s for interval in intervals if interval.kind == kind)

    def _metres(kind: str) -> float:
        return sum(interval.distance_m for interval in intervals if interval.kind == kind)

    moving_s = _seconds("moving")
    if abs(sum(sample.moving_s for sample in slices) - moving_s) > 1.0:
        raise RuntimeError(f"{path.name}: moving time was lost while slicing")

    fast_m = _metres("fast")
    speeds = sorted(
        interval.speed_kmh for interval in intervals if interval.dt_s > 0 and interval.distance_m > 0
    )
    return Activity(
        name=path.stem,
        source=path,
        copies=copies,
        started=timed[0].when,
        finished=timed[-1].when,
        course_m=course[-1].distance_m,
        slices=slices,
        moving_s=moving_s,
        stopped_s=_seconds("stopped"),
        aid_s=_seconds("aid"),
        glitch_s=_seconds("glitch"),
        glitch_m=_metres("glitch"),
        spike_m=_metres("spike"),
        fast_m=fast_m,
        fast_s=_seconds("fast"),
        fast_grades=_fast_grade_totals(intervals, segments),
        zero_dt_steps=sum(1 for interval in intervals if interval.dt_s <= 0 or interval.kind == "glitch"),
        speed_p50=_percentile(speeds, 0.50),
        speed_p90=_percentile(speeds, 0.90),
        speed_max=speeds[-1] if speeds else 0.0,
        in_pool=fast_m < NON_RUNNING_FAST_M,
        flat_pace_min_per_km=flat_pace,
        anchor_m=anchor_m,
        anchor_window=anchor_window,
        clamped_slices=clamped,
    )


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    index = min(len(values) - 1, max(0, round(fraction * (len(values) - 1))))
    return values[index]


def _fast_grade_totals(
    intervals: list[Interval],
    segments: list,
) -> tuple[tuple[float, float, float], ...]:
    """Sustained fast distance and time, grouped by the engine slice's grade window."""

    totals: dict[float, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for interval in intervals:
        if interval.kind != "fast" or interval.distance_m <= 0:
            continue
        grade = segments[_segment_index_at(segments, interval.mid_m)].grade_percent
        window = totals[_bucket_lo(grade)]
        window[0] += interval.distance_m
        window[1] += interval.dt_s
    return tuple((lo, totals[lo][0], totals[lo][1]) for lo in sorted(totals))


def collect_buckets(activities: list[Activity]) -> list[Bucket]:
    """Per-recording buckets, plus a pool of the recordings that are runs."""

    buckets: list[Bucket] = []
    windows: dict[float, list[MeasuredSlice]] = defaultdict(list)
    for activity in activities:
        grouped: dict[float, list[MeasuredSlice]] = defaultdict(list)
        for sample in activity.slices:
            lo = _bucket_lo(sample.grade_percent)
            grouped[lo].append(sample)
            if activity.in_pool:
                windows[lo].append(sample)
        for lo in sorted(grouped):
            buckets.append(_bucket_from(activity.name, lo, grouped[lo], activity.in_pool))
    for lo in sorted(windows):
        buckets.append(_bucket_from("ALL", lo, windows[lo], True))
    return buckets


def _bucket_from(activity: str, lo: float, samples: list[MeasuredSlice], in_pool: bool) -> Bucket:
    bucket = Bucket(activity=activity, lo=lo, hi=lo + BUCKET_WIDTH, in_running_pool=in_pool)
    seen: set[tuple[str, int]] = set()
    for sample in samples:
        seen.add((sample.activity, sample.segment_index))
        bucket.moving_m += sample.moving_m
        bucket.moving_s += sample.moving_s
        bucket.predicted_s += sample.predicted_s()
        bucket.flat_s += (sample.moving_m / 1000.0) * sample.flat_pace_min_per_km * 60.0
        bucket.grade_distance_m += sample.grade_percent * sample.moving_m
        bucket.factor_distance += sample.minetti_factor * sample.moving_m
    bucket.slices = len(seen)
    return bucket


def factor_with_raised_floor(grade_percent: float, min_cost_j: float) -> float:
    """Minetti factor if the cost floor were higher than today's 1.0 J/kg/m.

    metabolic_cost already applies today's floor. Raising the floor is
    max(today's cost, new floor) / C(0), which matches max(polynomial, new
    floor) / C(0) for every new floor at or above 1.0.
    """

    if min_cost_j < MIN_COST_J_PER_KG_M:
        raise ValueError("a what-if floor has to stay at or above the engine floor")
    cost = max(metabolic_cost_j_per_kg_m(grade_percent / 100.0), min_cost_j)
    return cost / FLAT_COST_J_PER_KG_M


def factor_with_hike(grade_percent: float, threshold: float, multiplier: float) -> float:
    """Today's factor, multiplied once the net grade reaches a hiking threshold."""

    factor = gap_cost_factor(grade_percent)
    if grade_percent >= threshold:
        return factor * multiplier
    return factor


def factor_with_descent_technicality(grade_percent: float, level: float) -> float:
    """Move steep-descent cost toward flat. Climbs stay on today's polynomial."""

    factor = gap_cost_factor(grade_percent)
    if grade_percent <= -8.0 and factor < 1.0:
        return factor + level * (1.0 - factor)
    return factor


def factor_with_technicality(grade_percent: float, level: float) -> float:
    """A 0–1 trail-roughness knob. 0 leaves Minetti unchanged.

    At or below -8% the downhill discount shrinks toward flat cost, and never
    makes a descent faster than Minetti. At or above +15% time is stretched
    by 1 + 0.40 * level. Grades between those lines stay on the polynomial.
    """

    factor = gap_cost_factor(grade_percent)
    if grade_percent <= -8.0 and factor < 1.0:
        return factor + level * (1.0 - factor)
    if grade_percent >= 15.0:
        return factor * (1.0 + 0.40 * level)
    return factor


@dataclass(frozen=True)
class Scenario:
    name: str
    detail: str
    adjust: object


def scenario_table(samples: list[MeasuredSlice]) -> list[str]:
    """What each candidate would do to duration error. Constants stay put."""

    scenarios = [
        Scenario("baseline", "engine constants, unchanged", lambda grade: gap_cost_factor(grade)),
        Scenario(
            "descent floor 1.6 J/kg/m",
            "raise MIN_COST from 1.0 to 1.6 (factor floor 0.44)",
            lambda grade: factor_with_raised_floor(grade, 1.6),
        ),
        Scenario(
            "descent floor 2.0 J/kg/m",
            "raise MIN_COST from 1.0 to 2.0 (factor floor 0.56)",
            lambda grade: factor_with_raised_floor(grade, 2.0),
        ),
        Scenario(
            "descent floor 2.5 J/kg/m",
            "raise MIN_COST from 1.0 to 2.5 (factor floor 0.69)",
            lambda grade: factor_with_raised_floor(grade, 2.5),
        ),
        Scenario(
            "descent floor 3.2 J/kg/m",
            "raise MIN_COST from 1.0 to 3.2 (factor floor 0.89)",
            lambda grade: factor_with_raised_floor(grade, 3.2),
        ),
        Scenario(
            "hike x1.25 above +15%",
            "multiply cost by 1.25 when net grade is at least +15%",
            lambda grade: factor_with_hike(grade, 15.0, 1.25),
        ),
        Scenario(
            "hike x1.40 above +15%",
            "multiply cost by 1.40 when net grade is at least +15%",
            lambda grade: factor_with_hike(grade, 15.0, 1.40),
        ),
        Scenario(
            "hike x1.25 above +20%",
            "multiply cost by 1.25 when net grade is at least +20%",
            lambda grade: factor_with_hike(grade, 20.0, 1.25),
        ),
        Scenario(
            "descent technicality 1.00",
            "below -8%, drop the downhill discount and use flat cost; climbs unchanged",
            lambda grade: factor_with_descent_technicality(grade, 1.0),
        ),
        Scenario(
            "technicality 0.50",
            "half of the downhill discount removed below -8%; climbs >= +15% take 1.20x",
            lambda grade: factor_with_technicality(grade, 0.50),
        ),
        Scenario(
            "technicality 1.00",
            "no downhill discount below -8%; climbs >= +15% take 1.40x",
            lambda grade: factor_with_technicality(grade, 1.00),
        ),
    ]
    lines = [
        "",
        "What-if adjustments on the running pool only (not applied to the engine).",
        "Error is predicted minus actual moving minutes. Negative means the model is too fast.",
        _scenario_header(),
    ]
    for scenario in scenarios:
        lines.append(_scenario_row(scenario, samples))
    lines.append("")
    lines.extend(_fit_notes(samples))
    return lines


def _group_error(samples: list[MeasuredSlice], adjust, predicate) -> tuple[float, float]:
    actual_s = 0.0
    predicted_s = 0.0
    for sample in samples:
        if not predicate(sample.grade_percent):
            continue
        actual_s += sample.moving_s
        predicted_s += (sample.moving_m / 1000.0) * sample.flat_pace_min_per_km * adjust(sample.grade_percent) * 60.0
    return (predicted_s - actual_s) / 60.0, actual_s / 60.0


def _scenario_header() -> str:
    return (
        f"{'scenario':<28} {'all min':>8} {'all %':>8} "
        f"{'<-10 min':>9} {'-10..0':>8} {'flat':>8} {'>=+15':>8} {'>=+20':>8}"
    )


def _scenario_row(scenario: Scenario, samples: list[MeasuredSlice]) -> str:
    groups = (
        lambda grade: True,
        lambda grade: grade < -10.0,
        lambda grade: -10.0 <= grade < 0.0,
        lambda grade: -5.0 <= grade <= 5.0,
        lambda grade: grade >= 15.0,
        lambda grade: grade >= 20.0,
    )
    cells = [f"{scenario.name:<28}"]
    for index, predicate in enumerate(groups):
        error_min, actual_min = _group_error(samples, scenario.adjust, predicate)
        if index == 0:
            cells.append(f"{error_min:8.1f}")
            percent = 0.0 if actual_min <= 0 else 100.0 * error_min / actual_min
            cells.append(f"{percent:7.1f}%")
        else:
            cells.append(f"{error_min:8.1f}")
    return " ".join(cells)


def _fit_notes(samples: list[MeasuredSlice]) -> list[str]:
    """The multiplier or floor that would zero one group's error, as a reading."""

    lines = [
        "Single-number fits that would zero one group's error. Side effects are in the grid above.",
    ]
    steep = [sample for sample in samples if sample.grade_percent < -10.0]
    climb_15 = [sample for sample in samples if sample.grade_percent >= 15.0]
    climb_20 = [sample for sample in samples if sample.grade_percent >= 20.0]
    lines.append(_floor_fit(steep))
    lines.append(_multiplier_fit(climb_15, "+15% and steeper"))
    lines.append(_multiplier_fit(climb_20, "+20% and steeper"))
    return lines


def _floor_fit(samples: list[MeasuredSlice]) -> str:
    if not samples:
        return "Steep descents (grade < -10%): no moving samples."
    actual_s = sum(sample.moving_s for sample in samples)
    # Search a floor that makes this group match. It may be below today's
    # cost on some slices and above it on others; the search only raises.
    low = MIN_COST_J_PER_KG_M
    high = FLAT_COST_J_PER_KG_M
    best = low
    best_abs = abs(_group_error(samples, lambda grade: factor_with_raised_floor(grade, low), lambda _g: True)[0])
    for _step in range(24):
        mid = (low + high) / 2.0
        error_min, _actual = _group_error(
            samples,
            lambda grade, floor=mid: factor_with_raised_floor(grade, floor),
            lambda _grade: True,
        )
        if abs(error_min) < best_abs:
            best_abs = abs(error_min)
            best = mid
        # A higher floor adds predicted time. Negative error means still too fast.
        if error_min < 0:
            low = mid
        else:
            high = mid
    error_min, actual_min = _group_error(
        samples,
        lambda grade, floor=best: factor_with_raised_floor(grade, floor),
        lambda _grade: True,
    )
    baseline, _actual = _group_error(samples, lambda grade: gap_cost_factor(grade), lambda _g: True)
    return (
        f"Steep descents (grade < -10%, {actual_s / 60.0:.1f} moving min): "
        f"baseline error {baseline:+.1f} min. "
        f"A floor of {best:.2f} J/kg/m (factor {best / FLAT_COST_J_PER_KG_M:.2f}) "
        f"leaves {error_min:+.1f} min on this group."
    )


def _multiplier_fit(samples: list[MeasuredSlice], label: str) -> str:
    if not samples:
        return f"{label}: no moving samples."
    actual_s = sum(sample.moving_s for sample in samples)
    predicted_s = sum(sample.predicted_s() for sample in samples)
    if predicted_s <= 0:
        return f"{label}: no predicted time."
    multiplier = actual_s / predicted_s
    error_min = (predicted_s - actual_s) / 60.0
    return (
        f"{label} ({actual_s / 60.0:.1f} moving min): baseline error {error_min:+.1f} min. "
        f"Multiplying those slices by {multiplier:.2f} would zero that group."
    )


def shared_trail_fraction(left: Activity, right: Activity, limit_m: float = 25.0) -> float:
    """Share of sampled points on `left` that pass within `limit_m` of `right`.

    This is a geometry check, not a claim that the two clocks are one effort.
    """

    left_course = _course_lat_lon(left.source)
    right_course = _course_lat_lon(right.source)
    if len(left_course) < 2 or len(right_course) < 2:
        return 0.0
    samples = left_course[:: max(1, len(left_course) // 160)]
    other = right_course[:: max(1, len(right_course) // 900)]
    close = 0
    for lat, lon in samples:
        nearest = min(_haversine_m(lat, lon, other_lat, other_lon) for other_lat, other_lon in other)
        if nearest <= limit_m:
            close += 1
    return close / len(samples)


def _course_lat_lon(path: Path) -> list[tuple[float, float]]:
    points = parse_gpx_xml(path.read_text(encoding="utf-8"))
    return [(point.lat, point.lon) for point in points]


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    a = min(1.0, max(0.0, a))
    return radius * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def render_report(
    activities: list[Activity],
    skipped: list[tuple[Path, str]],
    buckets: list[Bucket],
) -> str:
    lines: list[str] = []
    lines.append("Minetti calibration — recorded moving time vs engine relative cost")
    lines.append("Engine constants were not changed.")
    lines.append("")
    lines.append("Method")
    lines.append("  Slices: engine prepare_course + slice_segments (about 500 m, net grade).")
    lines.append(
        "  Excluded from moving time: speed under 1 km/h, aid dwells, non-positive timestamps,"
    )
    lines.append(
        f"  and isolated jumps (a stretch of at most {SPIKE_RUN_MAX_M:.0f} m peaking at "
        f"{SPIKE_SPEED_KMH:.0f} km/h or faster)."
    )
    lines.append(
        f"  Aid dwell: within {AID_RADIUS_M:.0f} m of a catalogued station, inside a "
        f"{AID_BAND_M:.0f} m band, for at least {AID_LINGER_S:.0f} s, slower than {AID_SHUFFLE_KMH:.0f} km/h."
    )
    lines.append(
        f"  Left out of the pool: a recording with at least {NON_RUNNING_FAST_M / 1000.0:.1f} km sustained "
        f"at or above {FAST_SPEED_KMH:.0f} km/h in stretches of {SUSTAINED_FAST_M:.0f} m."
    )
    lines.append("  Flat pace: each recording's own moving pace on slices graded -5% to +5%.")
    lines.append("  Prediction: moving km x that flat pace x gap_cost_factor(slice grade).")
    lines.append("  Error: predicted minutes minus actual moving minutes. Negative means the model is too fast.")
    lines.append(
        f"  Unreliable: fewer than {MIN_SLICES} slices, or under 1 km moving, or under 3 min moving."
    )
    lines.append("")
    lines.append("Files")
    if not activities:
        lines.append("  No timestamped GPX files.")
    for activity in activities:
        elapsed_h = (activity.finished - activity.started).total_seconds() / 3600.0
        lines.append(
            f"  {activity.name}: {activity.course_m / 1000.0:.2f} km, "
            f"{activity.started.isoformat()} -> {activity.finished.isoformat()} ({elapsed_h:.2f} h)"
        )
        lines.append(f"    source: {activity.source.as_posix()}")
        for copy in activity.copies:
            lines.append(f"    duplicate skipped: {copy.as_posix()}")
        lines.append(
            f"    moving {activity.moving_s / 60.0:.1f} min, "
            f"stopped {activity.stopped_s / 60.0:.1f} min, "
            f"aid dwell {activity.aid_s / 60.0:.1f} min, "
            f"spike {activity.spike_m:.0f} m, "
            f"zero-dt steps {activity.zero_dt_steps}"
        )
        lines.append(
            f"    speed p50 {activity.speed_p50:.1f} km/h, "
            f"p90 {activity.speed_p90:.1f}, max {activity.speed_max:.1f}"
        )
        if not activity.in_pool:
            lines.append(
                f"    NOT IN THE RUNNING POOL: {activity.fast_m / 1000.0:.2f} km "
                f"({activity.fast_s / 60.0:.1f} min) stayed at or above {FAST_SPEED_KMH:.0f} km/h "
                f"for stretches of at least {SUSTAINED_FAST_M:.0f} m."
            )
            lines.append(f"    Fast distance by grade: {_format_fast_grades(activity)}")
            lines.append(
                "    The grade table below drops those fast steps, so it is not a fair Minetti test."
            )
        lines.append(
            f"    flat pace {activity.flat_pace_min_per_km:.2f} min/km "
            f"from {activity.anchor_m / 1000.0:.2f} km in {activity.anchor_window}"
        )
        if activity.clamped_slices:
            lines.append(f"    slices steeper than ±40% (engine clamps the cost): {activity.clamped_slices}")
        if activity.in_pool:
            predicted_min = sum(sample.predicted_s() for sample in activity.slices) / 60.0
            actual_min = sum(sample.moving_s for sample in activity.slices) / 60.0
            error_min = predicted_min - actual_min
            error_pct = 100.0 * error_min / actual_min if actual_min else 0.0
            lines.append(
                f"    whole recording: predicted {predicted_min:.1f} min, "
                f"actual {actual_min:.1f} min, error {error_min:+.1f} min ({error_pct:+.1f}%)"
            )
        else:
            lines.append("    No whole-recording Minetti error. The fast stretches are not a running pace.")
    for path, reason in skipped:
        lines.append(f"  skipped {path.as_posix()}: {reason}")
    if len(activities) >= 2:
        lines.append("")
        lines.append("Shared trail (sampled points within 25 m of the other recording)")
        for index, left in enumerate(activities):
            for right in activities[index + 1 :]:
                share = shared_trail_fraction(left, right)
                lines.append(f"  {left.name} on {right.name}: {100.0 * share:.0f}%")
    lines.append("")
    lines.append("Engine cost factor at 5-point grades (unchanged)")
    for grade in range(-40, 45, 5):
        lines.append(f"  {grade:+4d}%  {gap_cost_factor(float(grade)):.3f}")
    pool_names = [activity.name for activity in activities if activity.in_pool]
    lines.append("")
    lines.append("Pooled grade buckets (" + ", ".join(pool_names) + ")")
    lines.extend(_bucket_table([bucket for bucket in buckets if bucket.activity == "ALL"]))
    for activity in activities:
        lines.append("")
        lines.append(f"{activity.name}")
        lines.extend(_bucket_table([bucket for bucket in buckets if bucket.activity == activity.name]))
    samples = [sample for activity in activities if activity.in_pool for sample in activity.slices]
    lines.extend(scenario_table(samples))
    lines.append("")
    return "\n".join(lines)


def _format_fast_grades(activity: Activity) -> str:
    parts: list[str] = []
    for lo, metres, seconds in activity.fast_grades:
        if metres < 50:
            continue
        pace = (seconds / 60.0) / (metres / 1000.0)
        parts.append(f"{_bucket_label(lo, lo + BUCKET_WIDTH)} {metres / 1000.0:.2f} km at {pace:.1f} min/km")
    return ", ".join(parts) if parts else "none"


def _bucket_table(buckets: list[Bucket]) -> list[str]:
    header = (
        f"{'bucket':<12} {'km':>6} {'min':>7} {'slices':>6} "
        f"{'actual':>7} {'minetti':>7} {'pred':>7} {'error':>7} {'error%':>7}  flag"
    )
    rows = [header]
    for bucket in buckets:
        actual = bucket.actual_factor
        minetti = bucket.minetti_factor
        error_pct = bucket.error_pct
        flag = "unreliable" if bucket.unreliable_reason else ""
        rows.append(
            f"{bucket.label:<12} {bucket.moving_m / 1000.0:6.2f} {bucket.actual_min:7.1f} {bucket.slices:6d} "
            f"{_num(actual):>7} {_num(minetti):>7} {bucket.predicted_min:7.1f} "
            f"{bucket.error_min:+7.1f} {_pct(error_pct):>7}  {flag}"
        )
        if bucket.unreliable_reason:
            rows.append(f"{'':<12} {bucket.unreliable_reason}")
    if len(rows) == 1:
        rows.append("  (no moving slices)")
    return rows


def _num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:+.1f}%"


def write_csv(path: Path, buckets: list[Bucket]) -> None:
    fieldnames = [
        "activity",
        "bucket",
        "grade_lo_pct",
        "grade_hi_pct",
        "slices",
        "moving_m",
        "moving_min",
        "mean_grade_pct",
        "actual_cost_factor",
        "minetti_cost_factor",
        "predicted_min",
        "time_error_min",
        "time_error_pct",
        "unreliable",
        "unreliable_reason",
        "in_running_pool",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for bucket in buckets:
            writer.writerow(
                {
                    "activity": bucket.activity,
                    "bucket": bucket.label,
                    "grade_lo_pct": f"{bucket.lo:.1f}",
                    "grade_hi_pct": f"{bucket.hi:.1f}",
                    "slices": bucket.slices,
                    "moving_m": f"{bucket.moving_m:.1f}",
                    "moving_min": f"{bucket.actual_min:.2f}",
                    "mean_grade_pct": "" if bucket.mean_grade is None else f"{bucket.mean_grade:.2f}",
                    "actual_cost_factor": "" if bucket.actual_factor is None else f"{bucket.actual_factor:.4f}",
                    "minetti_cost_factor": "" if bucket.minetti_factor is None else f"{bucket.minetti_factor:.4f}",
                    "predicted_min": f"{bucket.predicted_min:.2f}",
                    "time_error_min": f"{bucket.error_min:.2f}",
                    "time_error_pct": "" if bucket.error_pct is None else f"{bucket.error_pct:.2f}",
                    "unreliable": "yes" if bucket.unreliable_reason else "no",
                    "unreliable_reason": bucket.unreliable_reason,
                    "in_running_pool": "yes" if bucket.in_running_pool else "no",
                }
            )


def write_chart(path: Path, buckets: list[Bucket]) -> None:
    """Plot pooled relative cost and duration error. Matplotlib stays optional."""

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    pooled = [bucket for bucket in buckets if bucket.activity == "ALL"]
    figure, axes = plt.subplots(2, 1, figsize=(10.2, 8.2), sharex=True)
    grades = [index / 2 for index in range(-80, 81)]
    curve = [gap_cost_factor(grade) for grade in grades]
    axes[0].plot(grades, curve, color="#8b95a1", linewidth=1.5, label="Minetti C(grade) / C(0)")
    axes[0].axhline(1.0, color="#d0d5dd", linewidth=1.0)

    reliable = [bucket for bucket in pooled if not bucket.unreliable_reason and bucket.actual_factor is not None]
    thin = [bucket for bucket in pooled if bucket.unreliable_reason and bucket.actual_factor is not None]
    if reliable:
        axes[0].plot(
            [bucket.mean_grade for bucket in reliable],
            [bucket.actual_factor for bucket in reliable],
            "o",
            color="#14324f",
            markersize=7,
            label="Recorded relative cost",
        )
        axes[0].plot(
            [bucket.mean_grade for bucket in reliable],
            [bucket.minetti_factor for bucket in reliable],
            "s",
            color="#d4652f",
            markersize=6,
            label="Engine factor on these slices",
        )
    if thin:
        axes[0].plot(
            [bucket.mean_grade for bucket in thin],
            [bucket.actual_factor for bucket in thin],
            "o",
            markerfacecolor="none",
            color="#14324f",
            markersize=7,
            label="Unreliable bucket",
        )
    axes[0].set_ylabel("Minutes relative to flat pace")
    axes[0].set_title("Running recordings against Minetti, by 500 m net grade")
    axes[0].legend(frameon=False, fontsize=8)
    axes[0].grid(True, axis="y", color="#eef1f4")

    labels_grade = [bucket.mean_grade if bucket.mean_grade is not None else (bucket.lo + bucket.hi) / 2 for bucket in pooled]
    colors = ["#b7c0ca" if bucket.unreliable_reason else "#d4652f" for bucket in pooled]
    errors = [0.0 if bucket.error_pct is None else bucket.error_pct for bucket in pooled]
    axes[1].bar(labels_grade, errors, color=colors, width=6.0)
    axes[1].axhline(0.0, color="#14324f", linewidth=1.0)
    axes[1].set_ylabel("Predicted − actual (% of actual)")
    axes[1].set_xlabel("Net grade of the 500 m slice (%)")
    axes[1].set_xlim(-36, 42)
    axes[0].set_xlim(-36, 42)
    axes[1].grid(True, axis="y", color="#eef1f4")
    figure.text(
        0.01,
        0.01,
        "Gray bars: fewer than 4 slices, under 1 km, or under 3 min. "
        "Flat pace is each run's own moving pace from -5% to +5%. "
        "Stretches at or above 25 km/h are left out.",
        fontsize=8,
        color="#526070",
    )
    figure.tight_layout(rect=(0, 0.05, 1, 1))
    figure.savefig(path, dpi=140)
    plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare timestamped GPX moving time with Minetti cost.")
    parser.add_argument("--root", type=Path, default=ROOT, help="Folder to scan for .gpx files")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT, help="CSV, text, and chart directory")
    args = parser.parse_args()

    skipped: list[tuple[Path, str]] = []
    activities: list[Activity] = []
    for path, copies in choose_unique(find_gpx_files(args.root)):
        xml_text = path.read_text(encoding="utf-8")
        engine_count = len(parse_gpx_xml(xml_text))
        timed = parse_timed_points(path)
        if not timed:
            skipped.append((path, "no <time> on track points"))
            for copy in copies:
                skipped.append((copy, f"duplicate of {path.name}, which has no track <time>"))
            continue
        if len(timed) != engine_count:
            skipped.append((path, f"{len(timed)} of {engine_count} track points have <time>"))
            continue
        activity = measure_activity(path, copies)
        if activity is None:
            skipped.append((path, "no <time> on track points"))
            continue
        activities.append(activity)

    if not activities:
        print("No timestamped GPX files. Nothing was written.")
        for path, reason in skipped:
            print(f"  {path}: {reason}")
        return

    buckets = collect_buckets(activities)
    text = render_report(activities, skipped, buckets)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.out_dir / "pace-by-grade.csv"
    text_path = args.out_dir / "pace-by-grade.txt"
    chart_path = args.out_dir / "pace-by-grade.png"
    write_csv(csv_path, buckets)
    text_path.write_text(text, encoding="utf-8")
    try:
        write_chart(chart_path, buckets)
    except ModuleNotFoundError:
        print("matplotlib is not installed, so the chart was not written.")
        print("It is a dev-only tool: pip install matplotlib")
        chart_path = None
    print(text)
    print(f"CSV:  {csv_path}")
    print(f"Text: {text_path}")
    if chart_path is not None:
        print(f"Chart: {chart_path}")


if __name__ == "__main__":
    main()
