"""Unit and API tests for the terrain-aware fueling engine.

Duration scales in a straight line with flat GAP pace, so a target finish is
two plan builds: probe at 8 min/km, then pace = 8 * target / probe duration.
A red window feeds 15 g and drops the rest. Nothing is added to the next window.
GI Rescue is the `gi_rescue` flag on `POST /api/recalibrate`.
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from backend.fuel_engine import RED_MICRO_DOSE_G, classify_zone, generate_plan, snap_custom_aids, sweat_rates
from backend.models import AidStation, AthleteInput, CustomAidInput, DigestiveZone, FuelForm, PackedVestItem, TrackPoint
from backend.gpx_parser import location_at_distance, prepare_course
from backend.main import app

EARTH_RADIUS_M = 6_371_000.0
METERS_PER_DEG_LAT = EARTH_RADIUS_M * math.pi / 180.0
WINDOW_CARBS_G = 30.0  # 60 g/h over a 30 min feed

client = TestClient(app)


def course_points(sections: list[tuple[float, float]], step_m: float = 50.0) -> list[TrackPoint]:
    """Walk north from a fixed start. Each section is (length_m, grade_percent)."""

    lat = 42.58
    lon = 23.29
    elevation = 1500.0
    points = [TrackPoint(lat=lat, lon=lon, elevation_m=elevation)]
    for length_m, grade_percent in sections:
        steps = max(1, int(round(length_m / step_m)))
        rise_m = (grade_percent / 100.0) * (length_m / steps)
        dlat = (length_m / steps) / METERS_PER_DEG_LAT
        for _ in range(steps):
            lat += dlat
            elevation += rise_m
            points.append(TrackPoint(lat=lat, lon=lon, elevation_m=elevation))
    return points


def to_gpx(points: list[TrackPoint]) -> str:
    rows = "\n".join(
        f'<trkpt lat="{point.lat:.6f}" lon="{point.lon:.6f}"><ele>{point.elevation_m:.1f}</ele></trkpt>'
        for point in points
    )
    return f'<?xml version="1.0"?><gpx version="1.1"><trk><trkseg>{rows}</trkseg></trk></gpx>'


def build_plan(
    points: list[TrackPoint],
    athlete: AthleteInput,
    stations: list[AidStation] | None = None,
):
    return generate_plan(
        race_id="test-course",
        race_name="Synthetic course",
        declared_distance_km=0.0,
        aid_stations=stations or [],
        profile=points,
        athlete=athlete,
    )


def pace_for_target(points: list[TrackPoint], target_min: float) -> float:
    """Same two-call solve the app uses. Duration is linear in flat pace."""

    probe = build_plan(points, AthleteInput(flat_pace_min_per_km=8.0))
    pace = 8.0 * (target_min / probe.predicted_duration_min)
    return min(19.9, max(3.51, round(pace, 3)))


def minute_at(plan, distance_m: float) -> float:
    segment = next(
        item
        for item in plan.segments
        if item.start_distance_m <= distance_m <= item.end_distance_m
    )
    fraction = (distance_m - segment.start_distance_m) / segment.distance_m
    return segment.start_minute + fraction * segment.predicted_minutes


@pytest.mark.parametrize(
    ("grade", "zone"),
    [
        (-8.0, DigestiveZone.green),
        (0.0, DigestiveZone.green),
        (5.0, DigestiveZone.green),
        (5.1, DigestiveZone.yellow),
        (10.0, DigestiveZone.yellow),
        (-8.1, DigestiveZone.yellow),
        (-12.0, DigestiveZone.yellow),
        (10.1, DigestiveZone.red),
        (16.0, DigestiveZone.red),
        (-12.1, DigestiveZone.red),
        (-18.0, DigestiveZone.red),
    ],
)
def test_digestive_zone_boundaries(grade: float, zone: DigestiveZone) -> None:
    assert classify_zone(grade) is zone


@pytest.mark.parametrize(
    ("grade", "zone"),
    [
        (0.0, DigestiveZone.green),
        (7.0, DigestiveZone.yellow),
        (-10.0, DigestiveZone.yellow),
        (14.0, DigestiveZone.red),
        (-15.0, DigestiveZone.red),
    ],
)
def test_constant_grade_course_keeps_its_zone(grade: float, zone: DigestiveZone) -> None:
    plan = build_plan(course_points([(4_000, grade)]), AthleteInput(flat_pace_min_per_km=8.0))
    middle = plan.segments[len(plan.segments) // 2]
    assert middle.zone is zone
    assert middle.grade_percent == pytest.approx(grade, abs=0.4)


def test_target_time_solver_matches_segment_sum() -> None:
    points = course_points([(5_000, 8.0), (5_000, -4.0), (6_000, 0.0)])
    target_min = 180.0
    plan = build_plan(points, AthleteInput(flat_pace_min_per_km=pace_for_target(points, target_min)))
    segment_sum = sum(segment.predicted_minutes for segment in plan.segments)
    assert segment_sum == pytest.approx(plan.predicted_duration_min, abs=0.05)
    assert segment_sum == pytest.approx(target_min, abs=0.5)


def test_red_window_is_a_micro_dose_and_the_next_window_has_no_debt() -> None:
    points = course_points([(8_000, 15.0), (10_000, 0.0)])
    plan = build_plan(points, AthleteInput(weight_kg=70, carbs_g_per_hour=60, flat_pace_min_per_km=8.0))
    red_stops = [
        stop
        for stop in plan.fuel_stops
        if stop.zone is DigestiveZone.red and 1.0 < stop.distance_km < 8.0 and not stop.at_aid_station
    ]
    assert red_stops, "a sustained 15% climb should contain a red feed"
    opening = [stop for stop in red_stops if stop.scheduled_minute < 50]
    later = [stop for stop in red_stops if stop.scheduled_minute >= 50]
    assert all(stop.form is FuelForm.sip and stop.carbs_g == 0 for stop in opening)
    assert later, "the climb after the pre-race meal should still feed"
    assert all(stop.form is FuelForm.micro_dose for stop in later)
    assert all(stop.carbs_g == pytest.approx(RED_MICRO_DOSE_G, abs=0.1) for stop in later)

    green_after = [
        stop
        for stop in plan.fuel_stops
        if stop.zone is DigestiveZone.green and stop.distance_km > 8.2 and stop.carbs_g > 0
    ]
    assert green_after, "the flat finish should still schedule a green feed"
    assert green_after[0].carbs_g == pytest.approx(WINDOW_CARBS_G, abs=0.1)
    assert all(stop.carbs_g <= WINDOW_CARBS_G * 1.5 for stop in plan.fuel_stops)


def test_hot_weather_sweat_exceeds_cool_weather_for_70kg() -> None:
    cool_ml, cool_na = sweat_rates(10.0, 70.0)
    hot_ml, hot_na = sweat_rates(28.0, 70.0)
    assert cool_ml == pytest.approx(465.0)
    assert hot_ml == pytest.approx(875.0)
    assert hot_ml > cool_ml
    assert hot_na > cool_na

    cool = build_plan(course_points([(3_000, 0.0)]), AthleteInput(weight_kg=70, temperature_c=10))
    hot = build_plan(course_points([(3_000, 0.0)]), AthleteInput(weight_kg=70, temperature_c=28))
    assert hot.athlete.fluid_ml_per_hour > cool.athlete.fluid_ml_per_hour
    assert hot.athlete.sodium_mg_per_hour > cool.athlete.sodium_mg_per_hour


def test_long_race_turns_savoury_and_plates_food_stations() -> None:
    points = course_points([(32_000, 0.0)])
    course = prepare_course(points)
    pin = location_at_distance(course, 12_000)
    stations = [
        AidStation(
            name="Food hut",
            distance_km=12.0,
            lat=pin.lat,
            lon=pin.lon,
            elevation_m=pin.elevation_m,
            has_water=True,
            has_food=True,
        ),
        AidStation(
            name="Water only",
            distance_km=20.0,
            lat=pin.lat,
            lon=pin.lon,
            elevation_m=pin.elevation_m,
            has_water=True,
            has_food=False,
        ),
    ]
    plan = build_plan(
        points,
        AthleteInput(weight_kg=70, flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
        stations,
    )
    assert plan.predicted_duration_min > 210
    late = [
        stop
        for stop in plan.fuel_stops
        if stop.scheduled_minute >= 210 and not stop.at_aid_station and stop.carbs_g > 0
    ]
    assert late, "a race past 3.5 h should still have a feed after minute 210"
    assert any(stop.flavor == "Savoury/Salty" and stop.form is FuelForm.real_food for stop in late)
    early = [
        stop
        for stop in plan.fuel_stops
        if stop.scheduled_minute < 90 and not stop.at_aid_station and stop.carbs_g > 0
    ]
    assert early
    assert all("sandwich" not in stop.product.casefold() for stop in early)

    plates = [stop for stop in plan.fuel_stops if stop.at_aid_station]
    assert len(plates) == 1
    assert plates[0].product == "Aid station plate"
    assert plates[0].form is FuelForm.real_food
    assert plates[0].carbs_g == pytest.approx(28.0)
    food_leg = next(leg for leg in plan.aid_legs if leg.to_name == "Food hut")
    water_leg = next(leg for leg in plan.aid_legs if leg.to_name == "Water only")
    assert food_leg.station_carbs_g == pytest.approx(28.0)
    assert food_leg.vest_carbs_g + food_leg.carryover_carbs_g == pytest.approx(food_leg.carbs_g - 28.0)
    assert food_leg.carryover_carbs_g > 0
    assert water_leg.station_carbs_g == 0
    assert water_leg.vest_carbs_g + water_leg.carryover_carbs_g == pytest.approx(water_leg.carbs_g)
    assert all(stop.product != "Water only" for stop in plan.fuel_stops)


def test_recalibrate_gi_rescue_pauses_solids_then_halves_two_doses() -> None:
    points = course_points([(25_000, 0.0)])
    athlete = AthleteInput(weight_kg=70, temperature_c=18, flat_pace_min_per_km=8.0, carbs_g_per_hour=60)
    baseline = build_plan(points, athlete)
    snap = location_at_distance(prepare_course(points), 3_000)
    elapsed = minute_at(baseline, 3_000)
    body = {
        "gpx_xml": to_gpx(points),
        "athlete": athlete.model_dump(),
        "latitude": snap.lat,
        "longitude": snap.lon,
        "elapsed_min": elapsed,
    }

    rescued = client.post("/api/recalibrate", json={**body, "gi_distress": True})
    normal = client.post("/api/recalibrate", json={**body, "gi_rescue": False})
    assert rescued.status_code == 200, rescued.text
    assert normal.status_code == 200, normal.text
    rescued_body = rescued.json()
    normal_body = normal.json()
    assert rescued_body["gi_rescue_applied"] is True
    assert normal_body["gi_rescue_applied"] is False

    early = [
        stop
        for stop in rescued_body["remaining"]["fuel_stops"]
        if elapsed - 0.1 <= stop["ideal_minute"] < elapsed + 20
    ]
    assert early, "the first 20 minutes should still schedule sips"
    assert all(stop["carbs_g"] == 0 for stop in early)
    assert all(stop["form"] == FuelForm.rescue_sip.value for stop in early)
    assert all(stop["form"] not in {FuelForm.gel.value, FuelForm.real_food.value} for stop in early)

    def feeds_after_cooldown(payload: dict) -> list[dict]:
        return [
            stop
            for stop in payload["remaining"]["fuel_stops"]
            if stop["ideal_minute"] >= elapsed + 45 and stop["carbs_g"] > 0 and not stop["at_aid_station"]
        ]

    rescued_feeds = feeds_after_cooldown(rescued_body)
    normal_feeds = feeds_after_cooldown(normal_body)
    assert len(rescued_feeds) >= 3 and len(normal_feeds) >= 3
    assert rescued_feeds[0]["carbs_g"] == pytest.approx(normal_feeds[0]["carbs_g"] / 2, abs=0.2)
    assert rescued_feeds[1]["carbs_g"] == pytest.approx(normal_feeds[1]["carbs_g"] / 2, abs=0.2)
    assert rescued_feeds[2]["carbs_g"] == pytest.approx(normal_feeds[2]["carbs_g"], abs=0.2)
    assert "Half-size" in rescued_feeds[0]["reason"]


def test_custom_aid_station_snaps_to_the_nearest_trackpoint() -> None:
    points = course_points([(6_000, 2.0)])
    course = prepare_course(points)
    snapped = snap_custom_aids(
        course,
        [CustomAidInput(name="Bezbog", distance_km=2.03, has_food=True, cutoff_hours=6.5)],
    )
    nearest = min(course, key=lambda point: abs(point.distance_m - 2_030))
    assert snapped[0].name == "Bezbog"
    assert snapped[0].lat == nearest.lat
    assert snapped[0].lon == nearest.lon
    assert snapped[0].elevation_m == nearest.elevation_m
    assert snapped[0].distance_km == pytest.approx(nearest.distance_m / 1000.0)
    assert snapped[0].has_food is True
    assert snapped[0].cutoff_hours == pytest.approx(6.5)

    response = client.post(
        "/api/generate-plan",
        json={
            "gpx_xml": to_gpx(points),
            "athlete": AthleteInput(flat_pace_min_per_km=8.0).model_dump(),
            "aid_stations": [{"name": "Bezbog", "distance_km": 2.03, "has_water": True, "has_food": True, "cutoff_hours": 6.5}],
        },
    )
    assert response.status_code == 200, response.text
    names = [leg["to_name"] for leg in response.json()["aid_legs"]]
    assert "Bezbog" in names


def test_selected_vest_and_station_foods_change_the_plan() -> None:
    chips = build_plan(
        course_points([(24_000, 0.0)]),
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60, vest_foods=["chips"]),
    )
    late = [
        stop
        for stop in chips.fuel_stops
        if stop.scheduled_minute >= 150 and stop.carbs_g > 0 and not stop.at_aid_station
    ]
    assert late
    assert all(stop.product == "Salted Chips" for stop in late)
    assert all(stop.flavor == "Savoury/Salty" for stop in late)
    assert all(stop.form is FuelForm.real_food for stop in late)

    points = course_points([(20_000, 0.0)])
    course = prepare_course(points)
    pin = location_at_distance(course, 12_000)
    plated = build_plan(
        points,
        AthleteInput(
            flat_pace_min_per_km=8.0,
            station_foods=["watermelon", "cucumber"],
        ),
        [
            AidStation(
                name="Hut",
                distance_km=12.0,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        ],
    )
    leg = next(item for item in plated.aid_legs if item.to_name == "Hut")
    assert "Watermelon" in leg.station_meal
    assert "cucumber" in leg.station_meal.lower()
    assert leg.station_meal.count("(15g)") == 1
    assert leg.station_carbs_g == pytest.approx(15.0)
    assert leg.vest_carbs_g + leg.carryover_carbs_g == pytest.approx(leg.carbs_g - leg.station_carbs_g)


def test_custom_food_portions_keep_the_runners_name_and_grams() -> None:
    named = build_plan(
        course_points([(16_000, 0.0)]),
        AthleteInput(
            flat_pace_min_per_km=8.0,
            carbs_g_per_hour=60,
            vest_foods=["chocolate"],
            custom_vest_items=[
                {
                    "key": "chocolate",
                    "name": "Milka Chocolate",
                    "carbs_g": 30,
                    "sodium_mg": 20,
                    "flavor": "Sweet",
                }
            ],
        ),
    )
    greens = [
        stop
        for stop in named.fuel_stops
        if stop.zone is DigestiveZone.green and stop.carbs_g > 0 and not stop.at_aid_station
    ]
    assert greens
    assert all(stop.product == "Milka Chocolate" for stop in greens)
    assert all(stop.carbs_g == pytest.approx(30) for stop in greens)
    assert any(item.item == "Milka Chocolate" for leg in named.aid_legs for item in leg.pack)

    points = course_points([(20_000, 0.0)])
    course = prepare_course(points)
    pin = location_at_distance(course, 12_000)
    plated = build_plan(
        points,
        AthleteInput(
            flat_pace_min_per_km=8.0,
            station_foods=["chocolate", "cola_broth"],
            custom_station_items=[
                {"key": "chocolate", "name": "Trail Mix", "carbs_g": 22, "flavor": "Sweet"},
                {"key": "cola_broth", "name": "Warm Broth", "carbs_g": 18, "flavor": "Savoury/Salty"},
            ],
        ),
        [
            AidStation(
                name="Hut",
                distance_km=12.0,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        ],
    )
    leg = next(item for item in plated.aid_legs if item.to_name == "Hut")
    assert "Trail Mix" in leg.station_meal
    assert "Warm Broth" in leg.station_meal
    assert leg.station_carbs_g == pytest.approx(40)
    plate = next(stop for stop in plated.fuel_stops if stop.at_aid_station)
    assert plate.product == "Aid station plate"
    assert plate.carbs_g == pytest.approx(40)

    untouched = AthleteInput(flat_pace_min_per_km=8.0)
    assert untouched.custom_vest_items == []
    assert untouched.custom_station_items == []


def test_short_leg_does_not_schedule_a_station_meal() -> None:
    points = course_points([(8_000, 0.0)])
    course = prepare_course(points)
    pin = location_at_distance(course, 2_000)
    plan = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0),
        [
            AidStation(
                name="Close hut",
                distance_km=2.0,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        ],
    )
    leg = next(item for item in plan.aid_legs if item.to_name == "Close hut")
    assert leg.predicted_minutes < 40
    assert leg.station_meal == ""
    assert leg.station_carbs_g == 0
    assert not any(stop.at_aid_station for stop in plan.fuel_stops)


def test_long_leg_caps_flasks_and_food_types() -> None:
    plan = build_plan(
        course_points([(30_000, 0.0)]),
        AthleteInput(
            flat_pace_min_per_km=10.0,
            carbs_g_per_hour=60,
            temperature_c=18,
            vest_foods=["gel_25", "iso_gel_22", "drink_30", "bar", "sandwich", "chocolate"],
            custom_vest_items=[
                {"key": "gel_25", "name": "Standard Gel", "carbs_g": 25, "flavor": "Sweet"},
                {"key": "iso_gel_22", "name": "Isotonic Gel", "carbs_g": 22, "flavor": "Sweet"},
                {"key": "drink_30", "name": "Drink Mix", "carbs_g": 30, "flavor": "Sweet"},
                {"key": "bar", "name": "Oat Bar", "carbs_g": 30, "flavor": "Sweet"},
                {"key": "sandwich", "name": "Half Sandwich", "carbs_g": 20, "flavor": "Savoury/Salty"},
                {"key": "chocolate", "name": "Chocolate", "carbs_g": 15, "flavor": "Sweet"},
            ],
        ),
    )
    leg = plan.aid_legs[0]
    flasks = [item for item in leg.pack if "Flask" in item.item]
    assert len(flasks) == 1
    assert flasks[0].quantity in {2, 3}
    assert "8" not in str(flasks[0].quantity)
    assert "Refill" in flasks[0].detail
    assert "Drink Mix" in flasks[0].detail
    chewed = [item.item for item in leg.pack if "Flask" not in item.item and "Salt" not in item.item]
    assert chewed == ["Standard Gel", "Half Sandwich"] or set(chewed) <= {"Standard Gel", "Half Sandwich"}
    assert "Isotonic Gel" not in chewed
    assert "Oat Bar" not in chewed
    assert "Chocolate" not in chewed
    early = [
        stop
        for stop in plan.fuel_stops
        if stop.scheduled_minute < 90 and stop.carbs_g > 0 and not stop.at_aid_station
    ]
    assert early
    assert all("sandwich" not in stop.product.casefold() for stop in early)


def test_gut_calibration_scales_carbs_and_tightens_climbs() -> None:
    points = course_points([(5_000, 0.0)])
    base = build_plan(points, AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60))
    boosted = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60, carb_multiplier=1.12),
    )
    assert boosted.athlete.carbs_g_per_hour == pytest.approx(67.2)
    assert sum(leg.carbs_g for leg in boosted.aid_legs) == pytest.approx(
        sum(leg.carbs_g for leg in base.aid_legs) * 1.12,
        rel=0.02,
    )

    climb = course_points([(4_000, 4.0)])
    normal = build_plan(climb, AthleteInput(flat_pace_min_per_km=8.0, climb_sensitivity="normal"))
    sensitive = build_plan(climb, AthleteInput(flat_pace_min_per_km=8.0, climb_sensitivity="sensitive"))
    middle = len(normal.segments) // 2
    assert classify_zone(4.0) is DigestiveZone.green
    assert normal.segments[middle].zone is DigestiveZone.green
    assert sensitive.segments[middle].zone is DigestiveZone.yellow


def _pack_quantity(plan, needle: str) -> int:
    return sum(
        item.quantity
        for leg in plan.aid_legs
        for item in leg.pack
        if needle.casefold() in item.item.casefold()
    )


def test_short_race_carries_one_bite_and_skips_the_aid_table() -> None:
    points = course_points([(12_000, 0.0)])
    course = prepare_course(points)
    pin = location_at_distance(course, 6_000)
    station = AidStation(
        name="Hut",
        distance_km=6.0,
        lat=pin.lat,
        lon=pin.lon,
        elevation_m=pin.elevation_m,
        has_water=True,
        has_food=True,
    )
    plan = build_plan(
        points,
        AthleteInput(
            flat_pace_min_per_km=8.0,
            temperature_c=18,
            target_time_minutes=100,
            vest_foods=["gel_25", "drink_30", "bar", "sandwich", "dates"],
        ),
        [station],
    )
    assert any(line.startswith("Short Race Protocol") for line in plan.warnings)
    assert all(leg.station_meal == "" for leg in plan.aid_legs)
    assert all(leg.station_carbs_g == 0 for leg in plan.aid_legs)
    assert not any(stop.at_aid_station for stop in plan.fuel_stops)
    assert _pack_quantity(plan, "Flask") == 1
    assert _pack_quantity(plan, "Gel") == 1
    assert sum(stop.carbs_g for stop in plan.fuel_stops) <= 30
    names = [item.item for leg in plan.aid_legs for item in leg.pack]
    assert "Half Sandwich" not in names
    assert "Oat Bar" not in names

    hot = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0, temperature_c=28, target_time_minutes=90),
    )
    assert _pack_quantity(hot, "Flask") == 2
    assert _pack_quantity(hot, "Gel") == 1


def test_no_gels_never_schedules_a_gel_on_a_climb() -> None:
    points = course_points([(8_000, 15.0), (4_000, 0.0)])
    drink = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0, vest_foods=["drink_30", "dates", "bar"]),
    )
    red = [stop for stop in drink.fuel_stops if stop.zone is DigestiveZone.red and stop.carbs_g > 0]
    assert red, "a 15% climb should still feed"
    assert all(stop.product == "Drink Mix" for stop in red)
    assert all(stop.form is FuelForm.micro_dose for stop in red)
    assert all(stop.carbs_g == pytest.approx(15.0, abs=0.1) for stop in red)
    assert all("gel" not in stop.product.casefold() for stop in drink.fuel_stops)
    assert all("gel" not in item.item.casefold() for leg in drink.aid_legs for item in leg.pack)

    dates = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0, vest_foods=["dates"]),
    )
    red_dates = [stop for stop in dates.fuel_stops if stop.zone is DigestiveZone.red and stop.carbs_g > 0]
    assert red_dates
    assert all("date" in stop.product.casefold() for stop in red_dates)
    assert all("tiny bites" in stop.reason.casefold() for stop in red_dates)
    assert all("gel" not in stop.product.casefold() for stop in dates.fuel_stops)
    assert all("gel" not in item.item.casefold() for leg in dates.aid_legs for item in leg.pack)


def test_salt_capsules_are_absent_unless_the_runner_packs_them() -> None:
    plain = build_plan(course_points([(12_000, 0.0)]), AthleteInput(flat_pace_min_per_km=8.0))
    assert all("capsule" not in item.item.casefold() for leg in plain.aid_legs for item in leg.pack)
    early = [stop for stop in plain.fuel_stops if stop.scheduled_minute < 50 and not stop.at_aid_station]
    assert early
    assert all(stop.carbs_g == 0 for stop in early)

    salted = build_plan(
        course_points([(12_000, 0.0)]),
        AthleteInput(flat_pace_min_per_km=8.0, has_salt_caps=True, salt_caps_qty=4, salt_cap_mg=200),
    )
    caps = [item for leg in salted.aid_legs for item in leg.pack if "capsule" in item.item.casefold()]
    assert caps
    assert sum(item.quantity for item in caps) == 4


def test_fast_descent_carries_no_trail_food() -> None:
    points = course_points([(5_000, 6.0), (8_000, -8.0)])
    course = prepare_course(points)
    pin = location_at_distance(course, 5_000)
    plan = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
        [
            AidStation(
                name="Summit",
                distance_km=5.0,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        ],
    )
    descent = next(leg for leg in plan.aid_legs if leg.from_name == "Summit")
    assert "Fast descent" in descent.note
    assert descent.vest_carbs_g == 0
    chewed = [item for item in descent.pack if "flask" not in item.item.casefold()]
    assert chewed == []
    trail = [
        stop
        for stop in plan.fuel_stops
        if descent.from_km - 0.05 <= stop.distance_km < descent.to_km and not stop.at_aid_station
    ]
    assert all(stop.carbs_g == 0 for stop in trail)


def test_packed_vest_alternates_and_is_finished() -> None:
    """3 gels, 2 sandwiches, and 2 bars are mixed across legs and all eaten."""

    points = course_points([(36_000, 0.0)])
    course = prepare_course(points)
    stations = []
    for name, km in (("Hut A", 12.0), ("Hut B", 24.0)):
        pin = location_at_distance(course, int(km * 1000))
        stations.append(
            AidStation(
                name=name,
                distance_km=km,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        )
    plan = generate_plan(
        race_id="test-course",
        race_name="Synthetic course",
        declared_distance_km=36.0,
        aid_stations=stations,
        profile=points,
        athlete=AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
        packed_vest_items=[
            PackedVestItem(id="gel", name="Standard Gel", carbs_g=25, qty=3, flavor="sweet", form="gel"),
            PackedVestItem(id="sandwich", name="Half Sandwich", carbs_g=20, qty=2, flavor="savoury", form="solid"),
            PackedVestItem(id="bar", name="Oat Bar", carbs_g=30, qty=2, flavor="sweet", form="solid"),
        ],
    )
    packed = [stop.product for stop in plan.fuel_stops if stop.reason.startswith("Packed vest")]
    assert packed.count("Standard Gel") == 3
    assert packed.count("Half Sandwich") == 2
    assert packed.count("Oat Bar") == 2
    for index in range(len(packed) - 2):
        assert len(set(packed[index:index + 3])) > 1
    legs_used = sum(
        1
        for leg in plan.aid_legs
        if {item.item for item in leg.pack} & {"Standard Gel", "Half Sandwich", "Oat Bar"}
    )
    assert legs_used >= 2
    assert "3/3 Standard Gel" in plan.vest_inventory_summary
    assert "2/2 Half Sandwich" in plan.vest_inventory_summary
    assert "2/2 Oat Bar" in plan.vest_inventory_summary
    assert "0 left at finish" in plan.vest_inventory_summary

    def packed_qty(name: str) -> int:
        return sum(item.quantity for leg in plan.aid_legs for item in leg.pack if item.item == name)

    assert packed_qty("Standard Gel") == 3
    assert packed_qty("Half Sandwich") == 2
    assert packed_qty("Oat Bar") == 2
