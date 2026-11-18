"""Digestive safety zones from the net grade of a slice."""

from __future__ import annotations

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

def _green_max_for(athlete: AthleteInput) -> float:
    """Sensitive guts lose solid-food green once the climb passes +3.5%."""

    if athlete.climb_sensitivity == "sensitive":
        return SENSITIVE_GREEN_MAX
    return GREEN_MAX_GRADE

def classify_zone(grade_percent: float, green_max: float = GREEN_MAX_GRADE) -> DigestiveZone:
    """Map net segment grade to a digestive safety zone.

    Green:  -8% <= grade <= green_max (default +5%). A gel is reasonable here.
    Yellow: above that line through +10%, or -12% <= grade < -8%. Liquids and a half dose.
    Red:    grade > +10% or grade < -12%. No full gel. A 15 g micro-dose at most.
    A sensitive gut passes green_max=3.5 so moderate climbs leave the green zone.
    """

    if GREEN_MIN_GRADE <= grade_percent <= green_max:
        return DigestiveZone.green
    uphill_yellow = green_max < grade_percent <= YELLOW_UPHILL_MAX
    downhill_yellow = YELLOW_DOWNHILL_MIN <= grade_percent < GREEN_MIN_GRADE
    if uphill_yellow or downhill_yellow:
        return DigestiveZone.yellow
    return DigestiveZone.red

def _zone_rank(zone: DigestiveZone) -> float:
    return {DigestiveZone.green: 0.0, DigestiveZone.yellow: 1.0, DigestiveZone.red: 2.0}[zone]
