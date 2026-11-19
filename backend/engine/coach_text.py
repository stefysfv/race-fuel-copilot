"""Coach sentences for aid legs and quiet stops."""

from __future__ import annotations

from backend.models import (
    AidLeg,
    AidStation,
    ForfeitedDose,
    FuelForm,
    FuelStop,
    ReasonCode,
    Segment,
)

from backend.engine.reasons import dropped, make_reason

from backend.engine.constants import (
    DESCENT_LOCK_GRADE,
    PRE_RACE_FOOD_STATION_MIN,
    PRE_RACE_QUIET_MIN,
    PRE_RACE_SHORT_LEG_MIN,
)

from backend.engine.timing import _average_grade


def _coach_note(
    *,
    opening: bool,
    from_name: str,
    to_name: str,
    minutes: float,
    grade: float,
    arrival_has_food: bool,
    departure_has_food: bool | None,
) -> str:
    """One sentence a mountain coach would say about this leg."""

    if opening and (minutes <= PRE_RACE_SHORT_LEG_MIN or (minutes <= PRE_RACE_FOOD_STATION_MIN and arrival_has_food)):
        return (
            "Pre-race meal covers the first 50 min. Sip flask on the climb; "
            "refuel at the station table at the top."
        )
    if grade < DESCENT_LOCK_GRADE:
        return _descent_coach_note(from_name, to_name, grade, departure_has_food)
    if minutes > 70:
        return (
            "Take gels on the power-hike and on flat green. "
            "Leave the steep red and the fast descents clear."
        )
    return "Short leg. Sip fluids and save the bigger meal for the station table."


def _quiet_trail_stop(
    stop: FuelStop,
    reason: str,
    code: ReasonCode,
    **params: float,
) -> ForfeitedDose | None:
    """Turn a trail feed into a water sip. Station plates stay on the table.

    A stop that already has no carbs keeps the sentence and does not become
    a forfeited row.
    """

    if stop.at_aid_station or stop.form is FuelForm.rescue_sip:
        return None
    removed = stop.carbs_g
    stop.carbs_g = 0
    stop.caffeine_mg = 0
    stop.form = FuelForm.sip
    stop.product = "Water"
    stop.flavor = ""
    stop.reason = reason
    if removed <= 0:
        return None
    stop.reasons.append(make_reason(code, **params))
    return dropped(stop.scheduled_minute, removed, code, **params)


def _quiet_opening_stops(stops: list[FuelStop]) -> list[ForfeitedDose]:
    """The pre-race meal covers the first 50 minutes. No gel or solid on the trail.

    The window is minute 0 to 50 on the race clock. A plan for the course
    still ahead starts at the runner's elapsed time, and that later start
    does not open a second 50 minute gap.
    """

    limit = PRE_RACE_QUIET_MIN
    forfeited: list[ForfeitedDose] = []
    for stop in stops:
        if stop.scheduled_minute < limit:
            row = _quiet_trail_stop(
                stop,
                "Pre-race meal covers this window. Water and electrolyte sip only.",
                ReasonCode.QUIET_WINDOW,
                quiet_until_min=limit,
            )
            if row is not None:
                forfeited.append(row)
    return forfeited


def _departure_has_food(stations: list[AidStation], from_name: str) -> bool | None:
    """Whether the station this leg leaves actually serves food.

    Start and a recalibration pin are not stations, so they return None.
    """

    if from_name in {"Start", "Current position"}:
        return None
    departed = next((station for station in stations if station.name == from_name), None)
    if departed is None:
        return None
    return departed.has_food


def _descent_coach_note(
    from_name: str,
    to_name: str,
    grade: float,
    departure_has_food: bool | None,
) -> str:
    """The grade here is the leg average, not the grade under one stop."""

    average = f"Fast descent. Leg averages {grade:.1f}%."
    if departure_has_food is False:
        return f"{average} {from_name} has water only. The next food is at {to_name}."
    if departure_has_food is True:
        return (
            f"{average} Don't waste tempo chewing on the trail. "
            f"Your {from_name} station food powers this segment."
        )
    return f"{average} Don't waste tempo chewing on the trail."


def _descent_stop_reason(leg: AidLeg, grade: float, stations: list[AidStation]) -> str:
    """Same fact as the coach note, written on each quieted stop."""

    average = f"Leg averages {grade:.1f}%."
    departure_has_food = _departure_has_food(stations, leg.from_name)
    if departure_has_food is False:
        return f"{average} {leg.from_name} has water only. The next food is at {leg.to_name}."
    if departure_has_food is True:
        return f"{average} No trail chewing — the {leg.from_name} station meal covers this leg."
    return f"{average} No trail chewing on this descent."


def _quiet_descent_stops(
    stops: list[FuelStop],
    legs: list[AidLeg],
    segments: list[Segment],
    stations: list[AidStation],
) -> list[ForfeitedDose]:
    """A fast descent is for pace, not for unwrapping food."""

    forfeited: list[ForfeitedDose] = []
    for leg in legs:
        grade = _average_grade(segments, leg.from_km * 1000.0, leg.to_km * 1000.0)
        if grade >= DESCENT_LOCK_GRADE:
            continue
        reason = _descent_stop_reason(leg, grade, stations)
        for stop in stops:
            if leg.from_km - 0.05 <= stop.distance_km < leg.to_km - 0.02:
                row = _quiet_trail_stop(stop, reason, ReasonCode.DESCENT_LOCK, grade_percent=grade)
                if row is not None:
                    forfeited.append(row)
    return forfeited
