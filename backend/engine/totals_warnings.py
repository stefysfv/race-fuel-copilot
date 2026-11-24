"""Delivered carbs, plan totals, and the warnings attached to a plan."""

from __future__ import annotations

from backend.models import (
    AidLeg,
    AidStation,
    AthleteInput,
    DeliveryTier,
    DigestiveZone,
    ForfeitedDose,
    FuelBudget,
    FuelForm,
    FuelStop,
    PlanTotals,
    Segment,
)

from backend.engine.food_library import lookup_food
from backend.engine.constants import (
    CAFFEINE_MG_PER_KG,
    DELIVERY_INFO_RATIO,
    DELIVERY_WARN_RATIO,
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


def _delivered_window(
    stops: list[FuelStop],
    start_minute: float,
    finish_minute: float,
) -> tuple[float, float]:
    """Carbs after the quiet window, and the minutes those carbs are spread over.

    An empty window returns `(0, 0)` so a stop sitting on the quiet minute is
    not counted when the finish is still inside that window.
    """

    active_min = _active_fuel_minutes(start_minute, finish_minute)
    if active_min <= 0:
        return 0.0, 0.0
    carbs = sum(stop.carbs_g for stop in stops if stop.scheduled_minute >= PRE_RACE_QUIET_MIN)
    return carbs, active_min


def _delivered_grams_per_hour(
    stops: list[FuelStop],
    start_minute: float,
    finish_minute: float,
) -> float:
    """Carbs scheduled after the quiet window, per hour until the finish."""

    carbs, active_min = _delivered_window(stops, start_minute, finish_minute)
    if active_min <= 0:
        return 0.0
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
    if not _is_short_race(athlete) and target > 0 and delivered_g_per_hour < DELIVERY_WARN_RATIO * target:
        notes.append(
            f"Delivered {delivered_g_per_hour:.0f} g/h after the pre-race window, "
            f"below {DELIVERY_WARN_RATIO:.0%} of the {target:.0f} g/h target."
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


def _stop_calories(stop: FuelStop) -> float:
    """Return exact item kilocalories when known, else estimate from carbs & form."""

    if stop.kcal > 0:
        return stop.kcal
    if stop.carbs_g <= 0:
        return 0.0
    prof = lookup_food("", stop.product)
    if prof is not None and prof.kcal > 0 and prof.carbs_g > 0:
        return prof.kcal * (stop.carbs_g / prof.carbs_g)
    factor = 5.2 if stop.form is FuelForm.real_food else 4.0
    return stop.carbs_g * factor


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
        calories_kcal=sum(_stop_calories(stop) for stop in stops),
        salt_caps_sodium_mg=(float(athlete.salt_caps_qty * athlete.salt_cap_mg) if athlete.has_salt_caps else 0.0),
        magnesium_mg=sum(stop.magnesium_mg for stop in stops),
        potassium_mg=sum(stop.potassium_mg for stop in stops),
        caffeine_cap_mg=athlete.weight_kg * CAFFEINE_MG_PER_KG,
    )


def _share_pct(part: float, whole: float) -> float:
    """Percent of `whole`, clamped so a rounding error cannot fail the schema."""

    if whole <= 0:
        return 0.0
    return min(100.0, max(0.0, 100.0 * part / whole))


def _delivery_tier(athlete: AthleteInput, target: float, delivered_g_per_hour: float) -> tuple[DeliveryTier, str]:
    """Info lives on the budget. The under-70% sentence stays in `plan.warnings`."""

    if _is_short_race(athlete) or target <= 0:
        return DeliveryTier.ok, ""
    if delivered_g_per_hour < DELIVERY_WARN_RATIO * target:
        return DeliveryTier.warn, ""
    if delivered_g_per_hour < DELIVERY_INFO_RATIO * target:
        note = (
            f"Delivered {delivered_g_per_hour:.0f} g/h after the pre-race window, "
            f"under {DELIVERY_INFO_RATIO:.0%} of the {target:.0f} g/h target."
        )
        return DeliveryTier.info, note
    return DeliveryTier.ok, ""


def _forfeited_by_code(forfeited: list[ForfeitedDose]) -> dict[str, float]:
    """Sum dropped grams by code. A code with nothing dropped is omitted."""

    totals: dict[str, float] = {}
    for item in forfeited:
        totals[item.code.value] = totals.get(item.code.value, 0.0) + item.carbs_g
    return {code: grams for code, grams in sorted(totals.items()) if grams > 0}


def _fuel_budget(
    stops: list[FuelStop],
    forfeited: list[ForfeitedDose],
    segments: list[Segment],
    athlete: AthleteInput,
    *,
    start_minute: float,
    finish_minute: float,
) -> FuelBudget:
    """Fill the additive budget from stops, forfeited rows, and segment zones."""

    target = _effective_carb_target(athlete)
    delivered_total, active_min = _delivered_window(stops, start_minute, finish_minute)
    delivered_rate = 0.0 if active_min <= 0 else delivered_total / (active_min / 60.0)
    target_total = target * (active_min / 60.0)
    ratio = delivered_rate / target if target > 0 else 0.0
    by_code = _forfeited_by_code(forfeited)
    distance_m = sum(segment.distance_m for segment in segments)
    minutes = sum(segment.predicted_minutes for segment in segments)
    red_distance = sum(segment.distance_m for segment in segments if segment.zone is DigestiveZone.red)
    red_minutes = sum(segment.predicted_minutes for segment in segments if segment.zone is DigestiveZone.red)
    tier, note = _delivery_tier(athlete, target, delivered_rate)
    return FuelBudget(
        target_g_per_h=target,
        delivered_g_per_h=delivered_rate,
        delivery_ratio=ratio,
        target_total_g=target_total,
        delivered_total_g=delivered_total,
        forfeited_total_g=sum(by_code.values()),
        forfeited_by_code=by_code,
        red_share_of_course_pct=_share_pct(red_distance, distance_m),
        red_share_of_time_pct=_share_pct(red_minutes, minutes),
        tier=tier,
        note=note,
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
