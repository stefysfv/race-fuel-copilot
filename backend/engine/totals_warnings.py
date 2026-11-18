"""Delivered carbs, plan totals, and the warnings attached to a plan."""

from __future__ import annotations

from backend.models import (
    AidLeg,
    AidStation,
    AthleteInput,
    DigestiveZone,
    FuelForm,
    FuelStop,
    PlanTotals,
    Segment,
)

from backend.engine.constants import (
    CAFFEINE_MG_PER_KG,
    PRE_RACE_QUIET_MIN,
)

from backend.engine.short_race import _is_short_race

from backend.engine.timing import _minute_at_distance


def _active_fuel_minutes(start_minute: float, finish_minute: float) -> float:
    """Minutes from the end of the pre-race quiet window to the finish.

    A plan that starts after that window counts its whole duration. A plan
    that ends inside the window counts as zero so the rate is never divided
    by nothing.
    """

    active_start = max(start_minute, PRE_RACE_QUIET_MIN)
    return max(0.0, finish_minute - active_start)


def _delivered_grams_per_hour(
    stops: list[FuelStop],
    start_minute: float,
    finish_minute: float,
) -> float:
    """Carbs scheduled after the quiet window, per hour until the finish."""

    active_min = _active_fuel_minutes(start_minute, finish_minute)
    if active_min <= 0:
        return 0.0
    carbs = sum(stop.carbs_g for stop in stops if stop.scheduled_minute >= PRE_RACE_QUIET_MIN)
    return carbs / (active_min / 60.0)


def _leg_minutes_after_quiet(leg: AidLeg, segments: list[Segment]) -> float:
    """How much of this leg sits after the pre-race quiet window."""

    start_min = _minute_at_distance(segments, leg.from_km * 1000.0)
    end_min = _minute_at_distance(segments, leg.to_km * 1000.0)
    return max(0.0, end_min - max(start_min, PRE_RACE_QUIET_MIN))


def _carbs_delivered_on_leg(leg: AidLeg, stops: list[FuelStop]) -> float:
    """Carbs on stops that fall on this leg after the quiet window."""

    return sum(
        stop.carbs_g
        for stop in stops
        if stop.scheduled_minute >= PRE_RACE_QUIET_MIN
        and leg.from_km - 0.05 <= stop.distance_km < leg.to_km
    )


def _effective_carb_target(athlete: AthleteInput) -> float:
    """Hourly target the plan was built against.

    `generate_plan` already stores carbs_g_per_hour * carb_multiplier on the
    athlete, so this is that product and must not be scaled a second time.
    """

    return athlete.carbs_g_per_hour


def _delivery_warnings(
    stops: list[FuelStop],
    legs: list[AidLeg],
    segments: list[Segment],
    athlete: AthleteInput,
    delivered_g_per_hour: float,
) -> list[str]:
    """Honest gaps. This does not add or move any fuel."""

    notes: list[str] = []
    target = _effective_carb_target(athlete)
    if not _is_short_race(athlete) and target > 0 and delivered_g_per_hour < 0.70 * target:
        notes.append(
            f"Delivered {delivered_g_per_hour:.0f} g/h after the pre-race window, "
            f"below 70% of the {target:.0f} g/h target."
        )
    for leg in legs:
        after_min = _leg_minutes_after_quiet(leg, segments)
        if after_min <= 60.0:
            continue
        if _carbs_delivered_on_leg(leg, stops) > 0:
            continue
        notes.append(
            f"{leg.from_name} → {leg.to_name}: {after_min:.0f} min after the pre-race window "
            "with 0 g carbs delivered."
        )
    return notes


def _totals(
    stops: list[FuelStop],
    athlete: AthleteInput,
    *,
    start_minute: float,
    finish_minute: float,
) -> PlanTotals:
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
        carbs_g_per_hour_delivered=_delivered_grams_per_hour(stops, start_minute, finish_minute),
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
