"""Digestive safety zones from the net grade of a slice.

The four edges default to the constants in `backend.engine.constants`.
A caller passes a number to move one edge. `None` keeps that constant.
"""

from __future__ import annotations

from typing import NamedTuple

from backend.gpx_parser import CourseSegment

from backend.models import (
    AthleteInput,
    DigestiveZone,
)

from backend.engine.constants import (
    GREEN_MAX_GRADE,
    GREEN_MIN_GRADE,
    SENSITIVE_GREEN_MAX,
    YELLOW_DOWNHILL_MIN,
    YELLOW_UPHILL_MAX,
)


class ZoneEdges(NamedTuple):
    """Resolved grade lines, in the order `classify_zone` accepts after the grade."""

    green_max: float
    green_min: float
    red_climb: float
    red_descent: float


def _green_max_for(athlete: AthleteInput) -> float:
    """Sensitive guts lose solid-food green once the climb passes +3.5%."""

    if athlete.climb_sensitivity == "sensitive":
        return SENSITIVE_GREEN_MAX
    return GREEN_MAX_GRADE


def _edge(value: float | None, constant: float) -> float:
    """`None` means the historical constant. A number replaces it."""

    return constant if value is None else value


def zone_edges_for(athlete: AthleteInput) -> ZoneEdges:
    """Resolve the athlete's four lines.

    An omitted `green_max_grade` still follows `climb_sensitivity`, so the
    existing sensitive control keeps +3.5%. An explicit green max replaces that.
    The other three omitted lines stay at −8%, +10%, and −12%.
    """

    if athlete.green_max_grade is None:
        green_max = _green_max_for(athlete)
    else:
        green_max = athlete.green_max_grade
    return ZoneEdges(
        green_max=green_max,
        green_min=_edge(athlete.green_min_grade, GREEN_MIN_GRADE),
        red_climb=_edge(athlete.red_climb_grade, YELLOW_UPHILL_MAX),
        red_descent=_edge(athlete.red_descent_grade, YELLOW_DOWNHILL_MIN),
    )


def classify_zone(
    grade_percent: float,
    green_max: float | None = None,
    green_min: float | None = None,
    red_climb: float | None = None,
    red_descent: float | None = None,
) -> DigestiveZone:
    """Map net segment grade to a digestive safety zone.

    Green:  green_min <= grade <= green_max. A gel is reasonable here.
    Yellow: above green_max through red_climb, or red_descent <= grade < green_min.
            Liquids and a half dose.
    Red:    grade > red_climb or grade < red_descent. No full gel.
    Each omitted edge uses today's constant: −8%, +5%, +10%, −12%.
    This function does not read `climb_sensitivity`. Callers that have an
    athlete pass `zone_edges_for` so a sensitive gut still gets +3.5%.
    """

    resolved_max = _edge(green_max, GREEN_MAX_GRADE)
    resolved_min = _edge(green_min, GREEN_MIN_GRADE)
    resolved_climb = _edge(red_climb, YELLOW_UPHILL_MAX)
    resolved_descent = _edge(red_descent, YELLOW_DOWNHILL_MIN)
    if resolved_min <= grade_percent <= resolved_max:
        return DigestiveZone.green
    uphill_yellow = resolved_max < grade_percent <= resolved_climb
    downhill_yellow = resolved_descent <= grade_percent < resolved_min
    if uphill_yellow or downhill_yellow:
        return DigestiveZone.yellow
    return DigestiveZone.red


def _zone_rank(zone: DigestiveZone) -> float:
    return {DigestiveZone.green: 0.0, DigestiveZone.yellow: 1.0, DigestiveZone.red: 2.0}[zone]


def _share_pct(part: float, whole: float) -> float:
    """Percent of `whole`, clamped the same way as the fuel-budget red share."""

    if whole <= 0:
        return 0.0
    return min(100.0, max(0.0, 100.0 * part / whole))


def preview_course_zones(segments: list[CourseSegment], athlete: AthleteInput) -> dict[str, object]:
    """Count distance in each zone. No pace, stops, or pack list."""

    edges = zone_edges_for(athlete)
    counts = {zone.value: 0 for zone in DigestiveZone}
    distance = {zone.value: 0.0 for zone in DigestiveZone}
    for segment in segments:
        zone = classify_zone(segment.grade_percent, *edges)
        counts[zone.value] += 1
        distance[zone.value] += segment.distance_m
    total = sum(distance.values())
    red = distance[DigestiveZone.red.value]
    return {
        "distance_m": total,
        "red_distance_m": red,
        "red_share_of_course_pct": _share_pct(red, total),
        "zone_counts": counts,
        "zone_distance_m": distance,
    }
