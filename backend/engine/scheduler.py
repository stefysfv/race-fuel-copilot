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
    ForfeitedDose,
    FuelForm,
    FuelStop,
    PackedVestItem,
    Race,
    RacePlan,
    ReasonCode,
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
    RESCUE_EASE_CARBS_G,
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

from backend.engine.reasons import dropped, make_reason, shift_reasons

from backend.engine.short_race import (
    _is_short_race,
    _limit_short_race,
    _short_race_bite,
    _short_race_stops,
    _stops_still_ahead,
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
    _fuel_budget,
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
    # The full clock starts at minute 0. Truncation keeps those same minutes
    # on the slices still ahead; it does not restart the clock.
    full_clock = build_timed_segments(slice_segments(course), athlete, pace_ratio)
    timed = full_clock
    if from_distance_m > 0:
        timed = _truncate_front(full_clock, course, from_distance_m)
    if not timed:
        raise ValueError("No course remaining from this point")

    athlete = _scaled_athlete(_with_sweat_rates(athlete))
    warnings = _distance_warnings(declared_distance_km, course[-1].distance_m / 1000.0)

    total_m = course[-1].distance_m
    stations = _stations_on_course(aid_stations, total_m, warnings)
    forfeited: list[ForfeitedDose] = []
    if _is_short_race(athlete):
        warnings.insert(0, SHORT_RACE_NOTE)
        bite = _short_race_bite(athlete)
        # The bite and the sip grid belong to the whole race. Dropping the
        # stops already behind the snap leaves the rest on their original minutes.
        stops = _stops_still_ahead(
            _short_race_stops(full_clock, course, athlete, bite),
            timed[0].start_minute,
        )
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
        # On pace, the remainder is the original plan with the finished stops
        # removed. Scheduling only the road ahead would forget the menu (a
        # solid just behind the snap would not count, so the next feed would
        # be a different food). Off pace still scales the slices that are
        # left. GI Rescue starts at the snap, so that plan stays a new
        # schedule rather than a suffix of the original one.
        on_pace_remainder = (
            from_distance_m > 0 and pace_ratio == 1.0 and rescue_start_min is None
        )
        clock = full_clock if on_pace_remainder else timed
        stops, forfeited = schedule_fuel(
            clock,
            course,
            athlete,
            not_before_min=0.0 if on_pace_remainder else not_before_min,
            rescue_start_min=rescue_start_min,
        )
        stops = _insert_aid_bites(stops, stations, clock, athlete)
        forfeited.extend(_quiet_opening_stops(stops))
        if on_pace_remainder:
            full_legs = build_aid_legs(
                stations,
                full_clock,
                athlete,
                from_distance_m=0.0,
                total_m=total_m,
                stops=stops,
            )
            forfeited.extend(_quiet_descent_stops(stops, full_legs, full_clock, stations))
            horizon = timed[0].start_minute
            stops = _stops_still_ahead(stops, horizon)
            forfeited = [row for row in forfeited if row.at_min + 1e-6 >= horizon]
        legs = build_aid_legs(
            stations,
            timed,
            athlete,
            from_distance_m=max(0.0, from_distance_m),
            total_m=total_m,
            stops=stops,
        )
        if not on_pace_remainder:
            forfeited.extend(_quiet_descent_stops(stops, legs, timed, stations))
        forfeited.sort(key=lambda item: (item.at_min, item.code.value, item.carbs_g))
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
    budget = _fuel_budget(
        stops,
        forfeited,
        timed,
        athlete,
        start_minute=clock_start,
        finish_minute=finish_minute,
    )
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
        forfeited=forfeited,
        aid_legs=legs,
        aid_stations=stations,
        totals=totals,
        fuel_budget=budget,
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
) -> tuple[list[FuelStop], list[ForfeitedDose]]:
    """Place each feed on the safest segment inside a +/- 6 minute window.

    Every window is planned on its own. A red window does not add carbs to
    the next green one.
    """

    if not segments:
        return [], []

    finish_min = segments[-1].end_minute
    targets = _feed_targets(
        finish_min=finish_min,
        interval_min=athlete.feed_interval_min,
        not_before_min=not_before_min,
        rescue_start_min=rescue_start_min,
    )
    gentle_left = RESCUE_GENTLE_FEEDS if rescue_start_min is not None else 0
    stops: list[FuelStop] = []
    forfeited: list[ForfeitedDose] = []
    caffeine_used = 0.0
    race_start = segments[0].start_minute
    menu_clock = _MenuClock()

    for target in targets:
        placed = _place_feed(segments, target.minute, prefer_safe=target.kind == "normal")
        if placed is None:
            missed = _unplaced_carbs(target.kind, athlete)
            row = dropped(target.minute, missed, ReasonCode.UNPLACED)
            if row is not None:
                forfeited.append(row)
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
        for carbs_g, code, params in dose.drops:
            row = dropped(when, carbs_g, code, **params)
            if row is not None:
                forfeited.append(row)
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
                reasons=shift_reasons(target.minute, when, segment.grade_percent, segment.index) + list(dose.reasons),
            )
        )
    return stops, forfeited


def _unplaced_carbs(kind: str, athlete: AthleteInput) -> float:
    """Grams a slot would have served when no segment sits inside the window.

    A nil rescue sip is 0 g, so it is not recorded. An ease sip is the 12 g
    that could not be placed. A normal slot is one full window.
    """

    if kind == "rescue_nil":
        return 0.0
    if kind == "rescue_ease":
        return RESCUE_EASE_CARBS_G
    return athlete.carbs_g_per_hour * athlete.feed_interval_min / 60.0


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
                targets.append(_Target(sip_at, kind, sip_at - rescue_start_min))
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
                reasons=[make_reason(ReasonCode.AID_RESTOCK, plate_carbs_g=plate_carbs)],
            )
        )
        for stop in stops:
            if stop.at_aid_station or stop.carbs_g <= 0:
                continue
            if abs(stop.scheduled_minute - minute) > 15:
                continue
            before = stop.carbs_g
            stop.carbs_g = max(0.0, stop.carbs_g - plate_carbs)
            stop.caffeine_mg = 0.0
            if stop.carbs_g < 5:
                stop.carbs_g = 0.0
                stop.form = FuelForm.sip
                stop.product = ""
                stop.reason = f"Skip the vest snack. {plate_label}"
            else:
                stop.reason += f" The aid-station plate covers {plate_carbs:.0f} g of this window."
            covered = before - stop.carbs_g
            if covered > 0:
                stop.reasons.append(
                    make_reason(ReasonCode.AID_COVERED, plate_carbs_g=plate_carbs, covered_g=covered)
                )
            break
    merged = stops + bites
    merged.sort(key=lambda stop: (stop.scheduled_minute, stop.distance_km))
    for index, stop in enumerate(merged, start=1):
        stop.sequence = index
    return merged
