"""Build a full plan and place the feeds on the race clock."""

from __future__ import annotations

from backend.gpx_parser import (
    CoursePoint,
    location_at_distance,
    prepare_course,
    slice_segments,
)

from backend.models import (
    AidStation,
    AthleteInput,
    FuelForm,
    FuelStop,
    PackedVestItem,
    Race,
    RacePlan,
    Segment,
    TrackPoint,
)

from backend.engine.coach_text import (
    _quiet_descent_stops,
    _quiet_opening_stops,
)

from backend.engine.constants import (
    AID_LEG_MIN_MINUTES,
    GAP_MODEL_NAME,
    RESCUE_COOLDOWN_MIN,
    RESCUE_GENTLE_FEEDS,
    RESCUE_NIL_CARB_MIN,
    RESCUE_SIP_INTERVAL_MIN,
    SHORT_RACE_NOTE,
    WINDOW_MIN,
)

from backend.engine.doses_menu import (
    _MenuClock,
    _Target,
    _align_reason_with_intake,
    _apply_food_preference,
    _apply_locker,
    _dose_for_target,
    _gels_selected,
    _scaled_athlete,
    station_plate,
)

from backend.engine.packing import (
    _consume_packed_vest,
    build_aid_legs,
)

from backend.engine.short_race import (
    _is_short_race,
    _limit_short_race,
    _short_race_bite,
    _short_race_stops,
)

from backend.engine.sweat import _with_sweat_rates

from backend.engine.timing import (
    _approach_minutes,
    _distance_at_minute,
    _place_feed,
    _truncate_front,
    build_timed_segments,
)

from backend.engine.totals_warnings import (
    _delivery_warnings,
    _distance_warnings,
    _stations_on_course,
    _totals,
    _zone_counts,
)


def generate_plan(
    *,
    race_id: str,
    race_name: str,
    declared_distance_km: float,
    aid_stations: list[AidStation],
    profile: list[TrackPoint],
    athlete: AthleteInput,
    pace_ratio: float = 1.0,
    not_before_min: float = 0.0,
    from_distance_m: float = 0.0,
    rescue_start_min: float | None = None,
    packed_vest_items: list[PackedVestItem] | None = None,
) -> RacePlan:
    """Build a fueling plan for a full course, or for the part still ahead."""

    course = prepare_course(profile)
    computed_km = course[-1].distance_m / 1000.0
    if declared_distance_km <= 0:
        declared_distance_km = computed_km
    timed = build_timed_segments(slice_segments(course), athlete, pace_ratio)
    if from_distance_m > 0:
        timed = _truncate_front(timed, course, from_distance_m)
    if not timed:
        raise ValueError("No course remaining from this point")

    athlete = _scaled_athlete(_with_sweat_rates(athlete))
    warnings = _distance_warnings(declared_distance_km, course[-1].distance_m / 1000.0)

    total_m = course[-1].distance_m
    stations = _stations_on_course(aid_stations, total_m, warnings)
    if _is_short_race(athlete):
        warnings.insert(0, SHORT_RACE_NOTE)
        bite = _short_race_bite(athlete)
        stops = _short_race_stops(timed, course, athlete, bite)
        legs = _limit_short_race(
            build_aid_legs(
                stations,
                timed,
                athlete,
                from_distance_m=max(0.0, from_distance_m),
                total_m=total_m,
                stops=stops,
            ),
            athlete,
            bite,
        )
    else:
        stops = schedule_fuel(
            timed,
            course,
            athlete,
            not_before_min=not_before_min,
            rescue_start_min=rescue_start_min,
        )
        stops = _insert_aid_bites(stops, stations, timed, athlete)
        _quiet_opening_stops(stops, timed[0].start_minute)
        legs = build_aid_legs(
            stations,
            timed,
            athlete,
            from_distance_m=max(0.0, from_distance_m),
            total_m=total_m,
            stops=stops,
        )
        _quiet_descent_stops(stops, legs, timed, stations)
    inventory_summary = ""
    # A short race stays on one flask and one bite. The vest list is ignored there.
    if not _is_short_race(athlete) and packed_vest_items:
        inventory_summary, extra_note = _consume_packed_vest(
            stops,
            legs,
            timed,
            course,
            packed_vest_items,
            athlete.feed_interval_min,
        )
        if extra_note:
            warnings.append(extra_note)
    gain_m = sum(segment.elevation_gain_m for segment in timed)
    clock_start = timed[0].start_minute
    finish_minute = timed[-1].end_minute
    duration = finish_minute - clock_start
    totals = _totals(stops, athlete, start_minute=clock_start, finish_minute=finish_minute)
    warnings.extend(
        _delivery_warnings(stops, legs, timed, athlete, totals.carbs_g_per_hour_delivered)
    )
    return RacePlan(
        race_id=race_id,
        race_name=race_name,
        athlete=athlete,
        gap_model=GAP_MODEL_NAME,
        window_min=int(WINDOW_MIN),
        declared_distance_km=declared_distance_km,
        distance_km=(total_m - max(from_distance_m, 0.0)) / 1000.0,
        elevation_gain_m=gain_m,
        clock_start_min=clock_start,
        predicted_duration_min=duration,
        zone_counts=_zone_counts(timed),
        segments=timed,
        fuel_stops=stops,
        aid_legs=legs,
        aid_stations=stations,
        totals=totals,
        warnings=warnings,
        vest_inventory_summary=inventory_summary,
    )


def generate_plan_for_race(race: Race, athlete: AthleteInput) -> RacePlan:
    """Build a plan from a race record in `races.json`."""

    return generate_plan(
        race_id=race.id,
        race_name=race.name,
        declared_distance_km=race.distance_km,
        aid_stations=race.aid_stations,
        profile=race.profile,
        athlete=athlete,
    )


def schedule_fuel(
    segments: list[Segment],
    course: list[CoursePoint],
    athlete: AthleteInput,
    not_before_min: float = 0.0,
    rescue_start_min: float | None = None,
) -> list[FuelStop]:
    """Place each feed on the safest segment inside a +/- 6 minute window.

    Every window is planned on its own. A red window does not add carbs to
    the next green one.
    """

    if not segments:
        return []

    finish_min = segments[-1].end_minute
    targets = _feed_targets(
        finish_min=finish_min,
        interval_min=athlete.feed_interval_min,
        not_before_min=not_before_min,
        rescue_start_min=rescue_start_min,
    )
    gentle_left = RESCUE_GENTLE_FEEDS if rescue_start_min is not None else 0
    stops: list[FuelStop] = []
    caffeine_used = 0.0
    race_start = segments[0].start_minute
    menu_clock = _MenuClock()

    for target in targets:
        placed = _place_feed(segments, target.minute, prefer_safe=target.kind == "normal")
        if placed is None:
            continue
        segment, when = placed
        dose = _dose_for_target(
            target=target,
            zone=segment.zone,
            grade_percent=segment.grade_percent,
            shift_min=when - target.minute,
            athlete=athlete,
            gentle_left=gentle_left,
        )
        gentle_left = dose.gentle_left
        if _gels_selected(athlete):
            product, caffeine_mg, reason = _apply_locker(
                dose,
                segments,
                when,
                race_start,
                finish_min,
                athlete,
                caffeine_used,
            )
        else:
            product, caffeine_mg, reason = "", 0.0, dose.reason
        caffeine_used += caffeine_mg
        # A caffeinated gel stays a gel. Every other green feed can rotate onto real food.
        food_name = ""
        flavor = "Sweet"
        if caffeine_mg > 0:
            flavor = "Sweet"
        else:
            food_name, flavor = _apply_food_preference(
                dose,
                grade_percent=segment.grade_percent,
                zone=segment.zone,
                when=when,
                finish_min=finish_min,
                athlete=athlete,
                clock=menu_clock,
            )
            reason = dose.reason
            if food_name:
                product = food_name
        named = product or food_name
        reason = _align_reason_with_intake(reason, dose.form, named, dose.carbs_g)
        distance_m = _distance_at_minute(segment, when)
        location = location_at_distance(course, distance_m)
        stops.append(
            FuelStop(
                sequence=len(stops) + 1,
                ideal_minute=target.minute,
                scheduled_minute=when,
                shift_minutes=when - target.minute,
                distance_km=distance_m / 1000.0,
                latitude=location.lat,
                longitude=location.lon,
                segment_index=segment.index,
                grade_percent=segment.grade_percent,
                zone=segment.zone,
                carbs_g=dose.carbs_g,
                fluid_ml=dose.fluid_ml,
                sodium_mg=dose.sodium_mg,
                form=dose.form,
                reason=reason,
                caffeine_mg=caffeine_mg,
                product=product or food_name,
                flavor=flavor,
            )
        )
    return stops


def _feed_targets(
    finish_min: float,
    interval_min: int,
    not_before_min: float,
    rescue_start_min: float | None,
) -> list[_Target]:
    """Ideal clock times. Rescue replaces the normal grid during its cooldown."""

    targets: list[_Target] = []
    cooldown_end = None
    if rescue_start_min is not None:
        cooldown_end = rescue_start_min + RESCUE_COOLDOWN_MIN
        sip_at = rescue_start_min
        while sip_at < cooldown_end and sip_at < finish_min - 2:
            if sip_at >= not_before_min - 1e-6:
                kind = "rescue_nil" if sip_at < rescue_start_min + RESCUE_NIL_CARB_MIN else "rescue_ease"
                targets.append(_Target(sip_at, kind))
            sip_at += RESCUE_SIP_INTERVAL_MIN

    ideal = float(interval_min)
    while ideal <= finish_min - 10:
        inside_rescue = cooldown_end is not None and rescue_start_min is not None and rescue_start_min <= ideal < cooldown_end
        if ideal >= not_before_min - 1e-6 and not inside_rescue:
            targets.append(_Target(ideal, "normal"))
        ideal += interval_min
    targets.sort(key=lambda target: (target.minute, target.kind != "normal"))
    return targets








def _insert_aid_bites(
    stops: list[FuelStop],
    stations: list[AidStation],
    segments: list[Segment],
    athlete: AthleteInput,
) -> list[FuelStop]:
    """Add a plate when the arriving leg lasted at least 40 minutes and has food.

    The plate is eaten while stopped, so a red grade at the pin does not
    turn it into a climbing micro-dose. A 15 minute hop does not get a meal.
    """

    if not segments:
        return stops
    plate_carbs, plate_label, plate_sodium = station_plate(athlete)
    bites: list[FuelStop] = []
    for station in stations:
        if not station.has_food:
            continue
        distance_m = station.distance_km * 1000.0
        if distance_m < segments[0].start_distance_m - 30:
            continue
        leg_minutes, minute = _approach_minutes(segments, stations, distance_m)
        if leg_minutes < AID_LEG_MIN_MINUTES:
            continue
        segment = next(
            (item for item in segments if item.start_distance_m <= distance_m <= item.end_distance_m),
            segments[-1],
        )
        bites.append(
            FuelStop(
                sequence=1,
                ideal_minute=minute,
                scheduled_minute=minute,
                shift_minutes=0.0,
                distance_km=station.distance_km,
                latitude=station.lat,
                longitude=station.lon,
                segment_index=segment.index,
                grade_percent=segment.grade_percent,
                zone=segment.zone,
                carbs_g=plate_carbs,
                fluid_ml=150.0,
                sodium_mg=plate_sodium,
                form=FuelForm.real_food,
                reason=plate_label,
                product="Aid station plate",
                flavor="Savoury/Salty",
                at_aid_station=True,
            )
        )
        for stop in stops:
            if stop.at_aid_station or stop.carbs_g <= 0:
                continue
            if abs(stop.scheduled_minute - minute) > 15:
                continue
            stop.carbs_g = max(0.0, stop.carbs_g - plate_carbs)
            stop.caffeine_mg = 0.0
            if stop.carbs_g < 5:
                stop.carbs_g = 0.0
                stop.form = FuelForm.sip
                stop.product = ""
                stop.reason = f"Skip the vest snack. {plate_label}"
            else:
                stop.reason += f" The aid-station plate covers {plate_carbs:.0f} g of this window."
            break
    merged = stops + bites
    merged.sort(key=lambda stop: (stop.scheduled_minute, stop.distance_km))
    for index, stop in enumerate(merged, start=1):
        stop.sequence = index
    return merged
