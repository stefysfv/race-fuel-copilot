"""Print a demo plan and assert the engine invariants. Run it; do not import it into a plan."""

from __future__ import annotations

import math

from backend.gpx_parser import (
    attach_track,
    haversine_m,
    parse_gpx_xml,
    prepare_course,
    slice_segments,
    smooth_elevation,
)

from backend.models import (
    AthleteInput,
    DigestiveZone,
    FuelForm,
    LockerItem,
    PackItem,
    RacePlan,
    Segment,
)

from backend.catalog import load_race_catalog

from backend.engine.constants import (
    AID_BITE_CARBS_G,
    CAFFEINE_MG_PER_KG,
    GEL_CARBS_G,
    PACK_BUFFER,
    RED_MICRO_DOSE_G,
    WINDOW_MIN,
)

from backend.engine.doses_menu import (
    _Target,
    _dose_for_target,
)

from backend.engine.gap import gap_cost_factor

from backend.engine.packing import _pack_items

from backend.engine.recalibrate import recalibrate

from backend.engine.scheduler import (
    generate_plan,
    generate_plan_for_race,
)

from backend.engine.sweat import sweat_rates

from backend.engine.timing import (
    _minute_at_distance,
    _place_feed,
    build_timed_segments,
)

from backend.engine.zones import classify_zone


def _format_pack_item(item: PackItem) -> str:
    if item.item.startswith("Drink mix"):
        return f"{item.quantity} g drink mix"
    return f"{item.quantity} x {item.item}"


def _format_hours(minutes: float) -> str:
    hours = int(minutes // 60)
    mins = int(round(minutes - hours * 60))
    if mins == 60:
        hours += 1
        mins = 0
    return f"{hours} h {mins:02d} min"


def _print_plan(plan: RacePlan) -> None:
    zones = plan.zone_counts
    print(f"\n=== {plan.race_name} ===")
    print(
        f"Distance {plan.distance_km:.2f} km | gain {plan.elevation_gain_m:.0f} m | "
        f"predicted {_format_hours(plan.predicted_duration_min)} | "
        f"effort {plan.athlete.flat_pace_min_per_km:.1f} min/km GAP"
    )
    print(
        f"Segments {len(plan.segments)} | "
        f"green {zones.get('green', 0)} | yellow {zones.get('yellow', 0)} | red {zones.get('red', 0)}"
    )
    print(
        f"Fuel stops {len(plan.fuel_stops)} | "
        f"carbs {plan.totals.carbs_g:.0f} g | fluid {plan.totals.fluid_ml:.0f} ml | "
        f"sodium {plan.totals.sodium_mg:.0f} mg | "
        f"on time {plan.totals.stops_on_time} | shifted {plan.totals.stops_shifted} | "
        f"deferred {plan.totals.stops_deferred}"
    )
    if plan.warnings:
        for warning in plan.warnings:
            print(f"  warning: {warning}")
    print("First fuel stops:")
    for stop in plan.fuel_stops[:6]:
        print(
            f"  #{stop.sequence:<2} t={stop.scheduled_minute:6.1f} min ({stop.shift_minutes:+5.1f}) "
            f"km {stop.distance_km:5.1f}  {stop.zone.value:<6} {stop.form.value:<11} "
            f"{stop.carbs_g:5.1f} g  {stop.reason}"
        )
    print("Aid-station legs:")
    for leg in plan.aid_legs:
        pack = ", ".join(_format_pack_item(item) for item in leg.pack) or "nothing extra"
        print(
            f"  {leg.from_name} -> {leg.to_name}: {leg.distance_km:.1f} km, "
            f"{leg.predicted_minutes:.0f} min, pack [{pack}]"
        )


def _demo_segment(
    index: int,
    start_min: float,
    end_min: float,
    zone: DigestiveZone,
    grade: float,
) -> Segment:
    """A tiny segment used only by the terminal self-check."""

    distance_m = 500.0
    minutes = end_min - start_min
    return Segment(
        index=index,
        start_distance_m=index * distance_m,
        end_distance_m=(index + 1) * distance_m,
        distance_m=distance_m,
        start_lat=42.6,
        start_lon=23.3,
        end_lat=42.6,
        end_lon=23.3,
        elevation_start_m=1000,
        elevation_end_m=1000 + grade * 5,
        elevation_gain_m=max(grade, 0) * 5,
        elevation_loss_m=max(-grade, 0) * 5,
        grade_percent=grade,
        zone=zone,
        cost_factor=gap_cost_factor(grade),
        predicted_minutes=minutes,
        start_minute=start_min,
        end_minute=end_min,
        predicted_pace_min_per_km=minutes / 0.5,
        gap_min_per_km=(minutes / 0.5) / gap_cost_factor(grade),
    )


def _check_invariants() -> None:
    """Fail the process if the sports-science rules drift."""

    one_degree_m = haversine_m(42.0, 23.0, 43.0, 23.0)
    if not 110_900 < one_degree_m < 111_400:
        raise AssertionError(f"Haversine 1 degree latitude was {one_degree_m:.1f} m")
    if haversine_m(42.0, 23.0, 42.0, 23.0) != 0.0:
        raise AssertionError("Haversine of identical points must be 0")

    smoothed = smooth_elevation([0, 0, 0, 100, 0, 0, 0], window=5)
    if smoothed[3] != 20:
        raise AssertionError(f"5-point smooth of a spike should be 20, got {smoothed[3]}")

    if gap_cost_factor(0) != 1.0:
        raise AssertionError("Flat GAP cost factor must be exactly 1")
    if not (gap_cost_factor(10) > 1.0 > gap_cost_factor(-10)):
        raise AssertionError("Uphill GAP must cost more than flat, and mild downhill less")
    if classify_zone(5.0) != DigestiveZone.green or classify_zone(5.9) != DigestiveZone.yellow:
        raise AssertionError("+5.0% must be green and +5.9% must be yellow")
    if classify_zone(10.0) != DigestiveZone.yellow or classify_zone(10.5) != DigestiveZone.red:
        raise AssertionError("+10.0% must be yellow and +10.5% must be red")
    if classify_zone(-8.0) != DigestiveZone.green or classify_zone(-12.0) != DigestiveZone.yellow:
        raise AssertionError("-8% must be green and -12% must be yellow")
    if classify_zone(-12.1) != DigestiveZone.red:
        raise AssertionError("A descent steeper than -12% must be red")
    cool_ml, _cool_na = sweat_rates(14.9, 70)
    mild_ml, mild_na = sweat_rates(18, 70)
    hot_ml, hot_na = sweat_rates(25.1, 70)
    if cool_ml != 450 + 3 * 5 or mild_ml != 670 or hot_ml != 850 + 5 * 5:
        raise AssertionError(f"Sweat rate ml/h was cool {cool_ml}, mild {mild_ml}, hot {hot_ml}")
    if not math.isclose(mild_na, 700 * 0.670) or not math.isclose(hot_na, 950 * hot_ml / 1000):
        raise AssertionError("Sodium must be the band's mg/L times hourly litres")

    # Window: ideal minute 8 overlaps a red slice ending at 10 and a green slice from 10.
    # The gel must move onto the green slice, and the shift must stay within 6 minutes.
    red = _demo_segment(0, 0, 10, DigestiveZone.red, 16)
    green = _demo_segment(1, 10, 20, DigestiveZone.green, 1)
    later_red = _demo_segment(2, 20, 40, DigestiveZone.red, 15)
    placed = _place_feed([red, green, later_red], ideal_min=8, prefer_safe=True)
    if placed is None or placed[0].zone != DigestiveZone.green:
        raise AssertionError("Feed at minute 8 should move onto the green segment")
    if abs(placed[1] - 8) > WINDOW_MIN:
        raise AssertionError("Window shift exceeded 6 minutes")

    only_red = _place_feed([red], ideal_min=5, prefer_safe=True)
    if only_red is None or only_red[0].zone != DigestiveZone.red:
        raise AssertionError("A fully red window should still return a sip location")

    athlete = AthleteInput(weight_kg=70, temperature_c=18, carbs_g_per_hour=60, feed_interval_min=30)
    window_carbs = 30.0
    red_dose = _dose_for_target(
        target=_Target(5, "normal"),
        zone=DigestiveZone.red,
        grade_percent=16,
        shift_min=0,
        athlete=athlete,
        gentle_left=0,
    )
    if red_dose.carbs_g != RED_MICRO_DOSE_G or red_dose.form != FuelForm.micro_dose:
        raise AssertionError("A red-only window must take a 15 g liquid micro-dose")
    if "forfeited" not in red_dose.reason or "15 g" not in red_dose.reason:
        raise AssertionError("A red window must forfeit the rest of its own carbs")
    green_dose = _dose_for_target(
        target=_Target(35, "normal"),
        zone=DigestiveZone.green,
        grade_percent=1,
        shift_min=0,
        athlete=athlete,
        gentle_left=0,
    )
    if green_dose.carbs_g != window_carbs:
        raise AssertionError("The next green window must stay at one dose, with no carb debt")
    packed = _pack_items(0.72 * 60 * PACK_BUFFER, 500, 400)
    gels = next(item.quantity for item in packed if item.item == "Gel")
    if gels != 2:
        raise AssertionError(f"A 0.72 h leg at 60 g/h with a 15% buffer must pack 2 gels, got {gels}")

    rescue = _dose_for_target(
        target=_Target(0, "rescue_nil"),
        zone=DigestiveZone.green,
        grade_percent=0,
        shift_min=0,
        athlete=athlete,
        gentle_left=2,
    )
    if rescue.carbs_g != 0 or rescue.form != FuelForm.rescue_sip:
        raise AssertionError("GI Rescue must open with a zero-carb sip")


def run_self_check() -> None:
    """Print both sample races and check the math. Run: python -m backend.fuel_engine"""

    _check_invariants()
    races = load_race_catalog()
    if len(races) < 4:
        raise AssertionError("races.json should contain the four Vitosha GPX races")

    athlete_42 = AthleteInput(weight_kg=68, temperature_c=18, flat_pace_min_per_km=7.5, carbs_g_per_hour=60)
    athlete_100 = AthleteInput(weight_kg=68, temperature_c=18, flat_pace_min_per_km=9.0, carbs_g_per_hour=70)

    expected_ids = (
        "salomon-vitosha-12",
        "salomon-vitosha-27",
        "salomon-vitosha-40",
        "vitosha-100",
    )
    by_id = {race.id: race for race in races}
    for race_id in expected_ids:
        if race_id not in by_id:
            raise AssertionError(f"races.json is missing {race_id}")
        if not by_id[race_id].gpx_file:
            raise AssertionError(f"{race_id} should point at a GPX file")

    athletes_by_race = {
        "salomon-vitosha-27": athlete_42,
        "vitosha-100": athlete_100,
    }
    for race_id, athlete in athletes_by_race.items():
        race = attach_track(by_id[race_id])
        by_id[race_id] = race
        if len(race.profile) < 2:
            raise AssertionError(f"{race.name} GPX did not load")
        measured_km = prepare_course(race.profile)[-1].distance_m / 1000.0
        if abs(measured_km - race.distance_km) / race.distance_km > 0.02:
            raise AssertionError(f"{race.name} length {measured_km:.2f} km disagrees with the catalog")
        plan = generate_plan_for_race(race, athlete)
        _print_plan(plan)
        _assert_plan(plan, require_red=race_id != "vitosha-100")
        if race_id == "salomon-vitosha-27" and not any(leg.station_meal for leg in plan.aid_legs):
            raise AssertionError("The 27 km course should plate food at a station after 75 minutes")
        if race_id == "vitosha-100" and not any(stop.flavor == "Savoury/Salty" for stop in plan.fuel_stops):
            raise AssertionError("A 15 hour race should rotate onto savoury food after 3.5 hours")

    for race_id in ("salomon-vitosha-12", "salomon-vitosha-40"):
        race = attach_track(by_id[race_id])
        measured_km = prepare_course(race.profile)[-1].distance_m / 1000.0
        if abs(measured_km - race.distance_km) / race.distance_km > 0.02:
            raise AssertionError(f"{race.name} length {measured_km:.2f} km disagrees with the catalog")

    gpx = """<?xml version="1.0" encoding="UTF-8"?>
    <gpx version="1.1" creator="race-fuel-copilot">
      <trk><name>smoke</name><trkseg>
        <trkpt lat="42.6250" lon="23.3110"><ele>900</ele></trkpt>
        <trkpt lat="42.6290" lon="23.3110"><ele>980</ele></trkpt>
        <trkpt lat="42.6330" lon="23.3110"><ele>1040</ele></trkpt>
      </trkseg></trk>
    </gpx>
    """
    parsed = parse_gpx_xml(gpx)
    if len(parsed) != 3:
        raise AssertionError("GPX smoke test did not return 3 points")
    gpx_plan = generate_plan(
        race_id="uploaded",
        race_name="Uploaded GPX",
        declared_distance_km=1,
        aid_stations=[],
        profile=parsed,
        athlete=AthleteInput(),
    )
    if not gpx_plan.segments:
        raise AssertionError("GPX course produced no segments")

    locker_athlete = athlete_42.model_copy(
        update={
            "locker": [
                LockerItem(name="Standard Gel", carbs_g=25, caffeine_mg=0, kind="gel"),
                LockerItem(name="Caffeinated Gel", carbs_g=25, caffeine_mg=100, kind="gel"),
            ]
        }
    )
    caffeinated = generate_plan_for_race(by_id["salomon-vitosha-27"], locker_athlete)
    cap = locker_athlete.weight_kg * CAFFEINE_MG_PER_KG
    if caffeinated.totals.caffeine_mg < 100:
        raise AssertionError("A course with a climb should schedule at least one caffeinated gel")
    if caffeinated.totals.caffeine_mg > cap + 0.1:
        raise AssertionError(f"Caffeine {caffeinated.totals.caffeine_mg:.0f} mg exceeds the {cap:.0f} mg cap")
    if abs(caffeinated.totals.caffeine_cap_mg - cap) > 0.1:
        raise AssertionError("Plan totals must publish the 5 mg/kg caffeine cap")
    for stop in caffeinated.fuel_stops:
        if stop.caffeine_mg > 0 and stop.form != FuelForm.gel:
            raise AssertionError("Caffeine belongs on a gel feed, not a sip or a red micro-dose")
    packed_names = [item.item for leg in caffeinated.aid_legs for item in leg.pack]
    if "Caffeinated Gel" not in packed_names:
        raise AssertionError("The packing list must include the caffeinated gels the schedule placed")

    race = by_id["salomon-vitosha-27"]
    course = prepare_course(race.profile)
    # A dense GPS file has many points in the first minutes. Sample mid-race
    # so the pace ratio is allowed to move (it stays 1.0 near the start).
    sample = course[len(course) // 3]
    baseline = build_timed_segments(slice_segments(course), athlete_42)
    predicted = _minute_at_distance(baseline, sample.distance_m)
    elapsed = predicted * 1.2
    updated = recalibrate(
        race_id=race.id,
        race_name=race.name,
        declared_distance_km=race.distance_km,
        aid_stations=race.aid_stations,
        profile=race.profile,
        athlete=athlete_42,
        latitude=sample.lat,
        longitude=sample.lon,
        elapsed_min=elapsed,
        gi_rescue=False,
    )
    print(
        f"\nRecalibrated {race.name} at km {updated.snap.distance_along_km:.1f}: "
        f"off course {updated.snap.off_course_m:.1f} m, pace ratio {updated.snap.pace_ratio:.2f}"
    )
    print(f"  {updated.message}")
    if updated.snap.off_course_m > 5:
        raise AssertionError("Snapping a track point should land within 5 m")
    if abs(updated.snap.distance_along_m - sample.distance_m) > 50:
        raise AssertionError("Snap distance drifted away from the sample point")
    if not math.isclose(updated.snap.pace_ratio, 1.2, rel_tol=0.02):
        raise AssertionError(f"Expected pace ratio near 1.2, got {updated.snap.pace_ratio}")
    if updated.remaining.fuel_stops and updated.remaining.fuel_stops[0].scheduled_minute + 1e-6 < elapsed:
        raise AssertionError("Recalibrated plan still contains a feed in the past")

    rescue = recalibrate(
        race_id=race.id,
        race_name=race.name,
        declared_distance_km=race.distance_km,
        aid_stations=race.aid_stations,
        profile=race.profile,
        athlete=athlete_42,
        latitude=sample.lat,
        longitude=sample.lon,
        elapsed_min=elapsed,
        gi_rescue=True,
    )
    first = rescue.remaining.fuel_stops[0]
    print(
        f"GI Rescue first stop at t={first.scheduled_minute:.1f} min: "
        f"{first.form.value}, {first.carbs_g:.0f} g. {first.reason}"
    )
    if first.carbs_g != 0 or first.form != FuelForm.rescue_sip:
        raise AssertionError("GI Rescue should open with a zero-carb sip")

    print("\nSelf-check passed.")


def _vest_carbs_packed(pack: list[PackItem]) -> float:
    """Carbs represented by vest food lines. Flasks and salt are not food."""

    servings = {
        "Gel": GEL_CARBS_G,
        "Energy bar": 35.0,
        "Isotonic gel": 22.0,
        "Salted rice cake": 20.0,
        "Fruit or dates": 25.0,
        "Pretzels or chips": 22.0,
        "PB and jam bite": 25.0,
        "Boiled potato": 25.0,
    }
    return sum(item.quantity * servings.get(item.item, 0.0) for item in pack)


def _assert_plan(plan: RacePlan, require_red: bool = True) -> None:
    segment_distance = sum(segment.distance_m for segment in plan.segments)
    course_distance = plan.distance_km * 1000.0
    # A full plan's slices partition the track. Remaining plans start mid-slice.
    if plan.clock_start_min == 0 and abs(segment_distance - course_distance) > 1:
        raise AssertionError(f"Segments do not cover the course ({segment_distance:.1f} vs {course_distance:.1f})")
    if plan.elevation_gain_m < 500:
        raise AssertionError(f"{plan.race_name} gain looks flat: {plan.elevation_gain_m:.0f} m")
    if plan.zone_counts.get("green", 0) < 1:
        raise AssertionError(f"{plan.race_name} should include green terrain")
    if require_red and plan.zone_counts.get("red", 0) < 1:
        raise AssertionError(f"{plan.race_name} should include red terrain")
    if len(plan.fuel_stops) < 3:
        raise AssertionError(f"{plan.race_name} produced too few fuel stops")
    for stop in plan.fuel_stops:
        if abs(stop.shift_minutes) > WINDOW_MIN + 1e-6:
            raise AssertionError(f"Shift of {stop.shift_minutes} min is outside the +/-6 min window")
        if (
            stop.zone == DigestiveZone.red
            and not stop.at_aid_station
            and stop.form not in {FuelForm.micro_dose, FuelForm.rescue_sip, FuelForm.sip}
        ):
            raise AssertionError("A red segment must be a micro-dose or a rescue sip")
        if stop.form == FuelForm.micro_dose and stop.carbs_g > RED_MICRO_DOSE_G:
            raise AssertionError("A red micro-dose is larger than 15 g")
        if stop.zone == DigestiveZone.yellow and stop.form == FuelForm.gel:
            raise AssertionError("A yellow segment was assigned a gel")
        if "carried over" in stop.reason or "held back" in stop.reason:
            raise AssertionError("A fuel window still carries carbohydrate debt")
    if not plan.aid_legs:
        raise AssertionError("Plan is missing aid-station legs")
    if plan.clock_start_min == 0:
        opening = plan.aid_legs[0]
        if opening.from_name != "Start" or opening.from_km != 0:
            raise AssertionError("A full plan must start at 0.0 km (Start)")
    fluid_per_hour, sodium_per_hour = sweat_rates(plan.athlete.temperature_c, plan.athlete.weight_kg)
    for leg in plan.aid_legs:
        hours = leg.predicted_minutes / 60.0
        expected_carbs = hours * plan.athlete.carbs_g_per_hour * PACK_BUFFER
        expected_fluid = hours * fluid_per_hour * PACK_BUFFER
        expected_sodium = hours * sodium_per_hour * PACK_BUFFER
        if abs(leg.carbs_g - expected_carbs) > 0.01 or abs(leg.fluid_ml - expected_fluid) > 0.01:
            raise AssertionError(f"{leg.from_name} -> {leg.to_name} is not duration x rate x 1.15")
        if abs(leg.sodium_mg - expected_sodium) > 0.01:
            raise AssertionError(f"{leg.from_name} sodium pack does not follow the temperature formula")
        accounted = leg.vest_carbs_g + leg.station_carbs_g + leg.carryover_carbs_g
        if abs(accounted - leg.carbs_g) > 0.5:
            raise AssertionError(f"{leg.from_name} vest, station, and carry-over do not add up to the leg carbs")
        if leg.station_carbs_g > AID_BITE_CARBS_G + 0.1:
            raise AssertionError("An aid-station plate is larger than 28 g")
        if not plan.athlete.locker and leg.vest_carbs_g >= 1:
            covered = _vest_carbs_packed(leg.pack)
            if covered + 1 < leg.vest_carbs_g:
                raise AssertionError(
                    f"{leg.from_name} vest packs {covered:.0f} g for {leg.vest_carbs_g:.0f} g"
                )
