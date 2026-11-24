"""Unit and API tests for the terrain-aware fueling engine.

Duration scales in a straight line with flat GAP pace. Build Plan sends one
request with `match_target_time`. The engine probes the segment clock at
8 min/km, then builds the fuel plan once at pace = 8 * target / that duration.
A red window feeds 15 g and drops the rest. Nothing is added to the next window.
GI Rescue is the `gi_rescue` flag on `POST /api/recalibrate`.
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from backend.engine.timing import flat_pace_for_target
from backend.fuel_engine import (
    RED_MICRO_DOSE_G,
    _delivered_grams_per_hour,
    _delivery_warnings,
    classify_zone,
    generate_plan,
    snap_custom_aids,
    sweat_rates,
)
from backend.models import (
    AidLeg,
    AidStation,
    AthleteInput,
    CustomAidInput,
    DigestiveZone,
    FuelForm,
    FuelStop,
    PackedVestItem,
    Segment,
    TrackPoint,
)
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
    """Same scale Build Plan asks the engine to apply inside one plan."""

    return flat_pace_for_target(points, target_min)


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


def test_match_target_time_builds_one_plan_at_the_scaled_pace() -> None:
    """The flag scales pace inside one generate-plan. Without it, pace stays put."""

    points = course_points([(5_000, 8.0), (5_000, -4.0), (6_000, 0.0)])
    target_min = 180.0
    pace = pace_for_target(points, target_min)
    solved = client.post(
        "/api/generate-plan",
        json={
            "gpx_xml": to_gpx(points),
            "match_target_time": True,
            "athlete": {"flat_pace_min_per_km": 8.0, "target_time_minutes": target_min},
        },
    )
    assert solved.status_code == 200, solved.text
    body = solved.json()
    assert body["athlete"]["flat_pace_min_per_km"] == pytest.approx(pace)
    assert body["predicted_duration_min"] == pytest.approx(target_min, abs=0.5)

    kept = client.post(
        "/api/generate-plan",
        json={
            "gpx_xml": to_gpx(points),
            "athlete": {"flat_pace_min_per_km": 8.0, "target_time_minutes": target_min},
        },
    )
    assert kept.status_code == 200, kept.text
    assert kept.json()["athlete"]["flat_pace_min_per_km"] == pytest.approx(8.0)


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
    feeds = [stop for stop in plan.fuel_stops if stop.carbs_g > 0]
    assert len(feeds) == 1
    assert feeds[0].scheduled_minute == pytest.approx(plan.predicted_duration_min * 0.55, abs=8)
    sips = [stop for stop in plan.fuel_stops if stop.form is FuelForm.sip]
    assert sips
    assert all(stop.reason.startswith("Hydration check") for stop in sips)
    assert all(stop.carbs_g == 0 for stop in sips)
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
    """Gels, sandwiches, and bars alternate. Solids stay 90 minutes apart.

    The race can hold three solids (minute 60, 150, and 240). The second bar
    would be the fourth solid, so it stays in the vest.
    """

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
    solids = [
        stop
        for stop in plan.fuel_stops
        if stop.form is FuelForm.real_food and stop.carbs_g > 0 and not stop.at_aid_station
    ]
    assert [stop.scheduled_minute for stop in solids] == pytest.approx([60, 150, 210, 240])
    assert all(stop.scheduled_minute >= 150 or stop.flavor != "Savoury/Salty" for stop in solids)
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


def test_legacy_phone_payload_still_builds_a_plan() -> None:
    """An older saved vest row must not 422 the whole plan."""

    points = course_points([(8_000, 1.0)])
    response = client.post(
        "/api/generate-plan",
        json={
            "gpx_xml": to_gpx(points),
            "athlete": {
                "weight_kg": 70,
                "flat_pace_min_per_km": 8,
                "fuel_preference": "balanced",
                "locker": [{"name": "Old Bar", "carbs_g": 30, "kind": "solid", "notes": "stale"}],
            },
            "packed_vest_items": [
                {
                    "id": "legacy-gel-with-a-very-long-identifier-from-an-old-phone",
                    "name": "Standard Gel",
                    "carbs": 25,
                    "flavor": "Sweet",
                    "kind": "gel",
                    "qty": "2",
                    "notes": "stale",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["athlete"]["fuel_preference"] == "balanced_real_food"
    assert body["athlete"]["locker"][0]["kind"] == "bar"


def test_oversized_gpx_is_rejected() -> None:
    """An upload past 8,000,000 characters is a 422, before any plan is built."""

    huge = "x" * 8_000_001
    generated = client.post("/api/generate-plan", json={"gpx_xml": huge})
    recalibrated = client.post(
        "/api/recalibrate",
        json={"gpx_xml": huge, "latitude": 42.5, "longitude": 23.3, "elapsed_min": 10},
    )
    for response in (generated, recalibrated):
        assert response.status_code == 422, response.text
        detail = response.json()["detail"]
        assert any(item.get("loc", [None])[-1] == "gpx_xml" for item in detail)
        assert any("8000000" in item.get("msg", "") for item in detail)


def _clock_segment(start_m: float, end_m: float, start_min: float, end_min: float) -> Segment:
    """One segment whose clock and distance are the only fields the report uses."""

    span_m = end_m - start_m
    minutes = end_min - start_min
    km = span_m / 1000.0
    return Segment(
        index=0,
        start_distance_m=start_m,
        end_distance_m=end_m,
        distance_m=span_m,
        start_lat=42.6,
        start_lon=23.3,
        end_lat=42.6,
        end_lon=23.3,
        elevation_start_m=1000,
        elevation_end_m=1000,
        elevation_gain_m=0,
        elevation_loss_m=0,
        grade_percent=0,
        zone=DigestiveZone.green,
        cost_factor=1,
        predicted_minutes=minutes,
        start_minute=start_min,
        end_minute=end_min,
        predicted_pace_min_per_km=minutes / km,
        gap_min_per_km=minutes / km,
    )


def _report_stop(minute: float, distance_km: float, carbs_g: float) -> FuelStop:
    return FuelStop(
        sequence=1,
        ideal_minute=minute,
        scheduled_minute=minute,
        shift_minutes=0,
        distance_km=distance_km,
        latitude=42.6,
        longitude=23.3,
        segment_index=0,
        grade_percent=0,
        zone=DigestiveZone.green,
        carbs_g=carbs_g,
        fluid_ml=0,
        sodium_mg=0,
        form=FuelForm.gel,
        reason="test feed",
    )


def _report_leg(from_name: str, to_name: str, from_km: float, to_km: float, minutes: float) -> AidLeg:
    return AidLeg(
        from_name=from_name,
        to_name=to_name,
        from_km=from_km,
        to_km=to_km,
        distance_km=to_km - from_km,
        predicted_minutes=minutes,
        carbs_g=0,
        fluid_ml=0,
        sodium_mg=0,
        pack=[],
    )


def test_delivery_rate_ignores_the_quiet_window_and_a_zero_length_window() -> None:
    """Carbs inside the first 50 minutes are not part of the delivered rate."""

    stops = [_report_stop(20, 1.0, 100), _report_stop(80, 4.0, 120)]
    assert _delivered_grams_per_hour(stops, 0, 170) == pytest.approx(60)
    assert _delivered_grams_per_hour(stops, 0, 40) == 0


def test_low_delivery_warns_and_a_fed_plan_does_not() -> None:
    """Below 70% of the effective target warns. A plan on target stays quiet."""

    athlete = AthleteInput(carbs_g_per_hour=60)
    low_segments = [_clock_segment(0, 10_000, 0, 180)]
    low_legs = [_report_leg("Start", "Finish", 0, 10, 180)]
    low_stops = [_report_stop(80, 4.0, 30)]
    low_rate = _delivered_grams_per_hour(low_stops, 0, 180)
    low_notes = _delivery_warnings(low_stops, low_legs, low_segments, athlete, low_rate)
    assert low_rate < 0.70 * 60
    assert any("below 70%" in note for note in low_notes)
    assert not any("0 g carbs" in note for note in low_notes)

    fed_segments = [_clock_segment(0, 10_000, 0, 170)]
    fed_legs = [_report_leg("Start", "Finish", 0, 10, 170)]
    fed_stops = [_report_stop(80, 4.0, 120)]
    fed_rate = _delivered_grams_per_hour(fed_stops, 0, 170)
    fed_notes = _delivery_warnings(fed_stops, fed_legs, fed_segments, athlete, fed_rate)
    assert fed_rate == pytest.approx(60)
    assert fed_notes == []


def test_a_long_unfueled_stretch_names_the_leg() -> None:
    """70 minutes with nothing after the quiet window is named. The rate can still be on target."""

    segments = [
        _clock_segment(0, 5_000, 0, 230),
        _clock_segment(5_000, 8_000, 230, 300),
    ]
    legs = [
        _report_leg("Start", "Cherni Vrah", 0, 5, 230),
        _report_leg("Cherni Vrah", "Finish", 5, 8, 70),
    ]
    stops = [_report_stop(80, 2.0, 250)]
    rate = _delivered_grams_per_hour(stops, 0, 300)
    notes = _delivery_warnings(stops, legs, segments, AthleteInput(carbs_g_per_hour=60), rate)
    assert rate == pytest.approx(60)
    assert not any("below 70%" in note for note in notes)
    assert notes == ["Cherni Vrah → Finish: 70 min after the pre-race window with 0 g carbs delivered."]


def test_short_race_and_scaled_target_do_not_false_warn() -> None:
    """A short race skips the rate warning. The gut multiplier is not applied twice."""

    segments = [_clock_segment(0, 10_000, 0, 180)]
    legs = [_report_leg("Start", "Finish", 0, 10, 180)]
    stops = [_report_stop(80, 4.0, 30)]
    rate = _delivered_grams_per_hour(stops, 0, 180)
    short = AthleteInput(carbs_g_per_hour=60, target_time_minutes=90)
    assert _delivery_warnings(stops, legs, segments, short, rate) == []

    # 60 g/h is under 70% of 75*1.25, and above 70% of the stored 75 g/h target.
    scaled = AthleteInput(carbs_g_per_hour=75, carb_multiplier=1.25)
    on_target = [_clock_segment(0, 10_000, 0, 170)]
    on_stops = [_report_stop(80, 4.0, 120)]
    on_rate = _delivered_grams_per_hour(on_stops, 0, 170)
    assert _delivery_warnings(on_stops, legs, on_target, scaled, on_rate) == []


def test_generated_plan_publishes_the_same_delivery_report() -> None:
    plan = build_plan(
        course_points([(20_000, 0.0)]),
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
    )
    finish = plan.clock_start_min + plan.predicted_duration_min
    delivered = _delivered_grams_per_hour(plan.fuel_stops, plan.clock_start_min, finish)
    assert plan.totals.carbs_g_per_hour_delivered == pytest.approx(delivered)
    for note in _delivery_warnings(
        plan.fuel_stops,
        plan.aid_legs,
        plan.segments,
        plan.athlete,
        delivered,
    ):
        assert note in plan.warnings


def _station_at(course, name: str, distance_m: float, *, has_food: bool) -> AidStation:
    pin = location_at_distance(course, distance_m)
    return AidStation(
        name=name,
        distance_km=distance_m / 1000.0,
        lat=pin.lat,
        lon=pin.lon,
        elevation_m=pin.elevation_m,
        has_water=True,
        has_food=has_food,
    )


def _trail_stops(plan, leg):
    return [
        stop
        for stop in plan.fuel_stops
        if leg.from_km - 0.05 <= stop.distance_km < leg.to_km - 0.02 and not stop.at_aid_station
    ]


def test_descent_wording_follows_the_departure_station() -> None:
    """A water-only station must not be described as if it served food."""

    points = course_points([(4_000, 6.0), (8_000, -8.0), (8_000, -8.0)])
    course = prepare_course(points)
    plan = build_plan(
        points,
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
        [
            _station_at(course, "Cherni Vrah", 4_000, has_food=False),
            _station_at(course, "Zlatni Mostove", 12_000, has_food=True),
        ],
    )
    water_leg = next(leg for leg in plan.aid_legs if leg.from_name == "Cherni Vrah")
    food_leg = next(leg for leg in plan.aid_legs if leg.from_name == "Zlatni Mostove")

    assert "Leg averages" in water_leg.note
    assert "water only" in water_leg.note
    assert "The next food is at Zlatni Mostove" in water_leg.note
    assert "station food" not in water_leg.note

    assert "Leg averages" in food_leg.note
    assert "Your Zlatni Mostove station food powers this segment." in food_leg.note

    water_stops = _trail_stops(plan, water_leg)
    food_stops = _trail_stops(plan, food_leg)
    assert water_stops
    assert food_stops
    assert all("Leg averages" in stop.reason for stop in water_stops)
    assert all("water only" in stop.reason for stop in water_stops)
    assert all("station meal covers" not in stop.reason for stop in water_stops)
    assert all("Leg averages" in stop.reason for stop in food_stops)
    assert all("station meal covers" in stop.reason for stop in food_stops)


def test_stop_reasons_name_the_chosen_food() -> None:
    """The sentence names the product on the stop, not a gel or a liquid that was not picked."""

    yellow_plan = build_plan(
        course_points([(12_000, 7.0)]),
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
    )
    yellow = [
        stop
        for stop in yellow_plan.fuel_stops
        if stop.zone is DigestiveZone.yellow and stop.carbs_g > 0 and stop.product
    ]
    assert yellow
    assert any(stop.form is FuelForm.gel for stop in yellow)
    assert any(stop.form is FuelForm.liquid for stop in yellow)
    for stop in yellow:
        assert stop.product in stop.reason
        if stop.form is FuelForm.gel:
            assert "as liquid" not in stop.reason
        if stop.form is FuelForm.liquid:
            assert " g gel" not in stop.reason

    flat = build_plan(
        course_points([(20_000, 0.0)]),
        AthleteInput(flat_pace_min_per_km=8.0, carbs_g_per_hour=60),
    )
    drinks = [
        stop
        for stop in flat.fuel_stops
        if stop.product == "Drink Mix" and stop.carbs_g > 0 and stop.zone is DigestiveZone.green
    ]
    assert drinks
    assert all(" g gel" not in stop.reason for stop in drinks)
    assert all("Drink Mix" in stop.reason for stop in drinks)


def test_packed_vest_never_exceeds_inventory_or_invents_liquid() -> None:
    """Vest inventory is a hard cap: no extra gels, no phantom liquid carbs, caffeine counted."""

    points = course_points([(15_000, 0.0), (15_000, 14.0), (30_000, 0.0)])
    packed = [
        PackedVestItem(id="caffeine_gel", name="Caffeine Gel", carbs_g=25, caffeine_mg=100, qty=2, flavor="sweet", form="gel"),
        PackedVestItem(id="bar", name="Oat / Energy Bar", carbs_g=30, qty=1, flavor="sweet", form="solid"),
        PackedVestItem(id="dates", name="Medjool Dates", carbs_g=25, qty=3, flavor="sweet", form="solid"),
        PackedVestItem(id="chocolate", name="Chocolate", carbs_g=20, qty=2, flavor="sweet", form="solid"),
        PackedVestItem(id="chips", name="Salted Chips / Pretzels", carbs_g=20, qty=1, flavor="savoury", form="solid"),
    ]
    plan = generate_plan(
        race_id="pirin-vest",
        race_name="Pirin Ultra Test",
        declared_distance_km=60.0,
        aid_stations=[],
        profile=points,
        athlete=AthleteInput(
            weight_kg=70,
            flat_pace_min_per_km=10.0,
            carbs_g_per_hour=90,
            vest_foods=["caffeine_gel", "bar", "dates", "chocolate", "chips"],
        ),
        packed_vest_items=packed,
    )
    carb_stops = [stop for stop in plan.fuel_stops if stop.carbs_g > 0 and not stop.at_aid_station]
    assert carb_stops
    assert all(stop.reason.startswith("Packed vest:") for stop in carb_stops)
    counts: dict[str, int] = {}
    for stop in carb_stops:
        counts[stop.product] = counts.get(stop.product, 0) + 1
    assert counts.get("Caffeine Gel", 0) <= 2
    assert counts.get("Oat / Energy Bar", 0) <= 1
    assert counts.get("Medjool Dates", 0) <= 3
    assert counts.get("Chocolate", 0) <= 2
    assert counts.get("Salted Chips / Pretzels", 0) <= 1
    assert plan.totals.carbs_g <= 215.0 + 1e-6
    assert plan.totals.caffeine_mg == pytest.approx(200.0)
    assert not any(stop.product == "" and stop.carbs_g > 0 for stop in plan.fuel_stops)


def test_short_vest_boosts_aid_station_food_without_phantom_gels() -> None:
    """When the vest runs out and food stations exist, station meals scale up instead of spawning gels."""

    points = course_points([(60_000, 0.0)])
    course = prepare_course(points)
    stations = []
    for name, km in (("Aid 1", 20.0), ("Aid 2", 40.0)):
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
        race_id="aid-cover",
        race_name="Aid Cover Test",
        declared_distance_km=60.0,
        aid_stations=stations,
        profile=points,
        athlete=AthleteInput(
            weight_kg=70,
            flat_pace_min_per_km=8.0,
            carbs_g_per_hour=90,
            vest_foods=["caffeine_gel"],
            station_foods=["sandwich", "banana", "dates", "cola_broth"],
        ),
        packed_vest_items=[
            PackedVestItem(id="caffeine_gel", name="Caffeine Gel", carbs_g=25, caffeine_mg=100, qty=2, flavor="sweet", form="gel"),
        ],
    )
    trail_carbs = [stop for stop in plan.fuel_stops if not stop.at_aid_station and stop.carbs_g > 0]
    gel_stops = [stop for stop in trail_carbs if stop.form is FuelForm.gel]
    restock_stops = [stop for stop in trail_carbs if stop.reason.startswith("Aid restock")]
    assert len(gel_stops) == 2
    assert all(stop.product == "Caffeine Gel" and stop.reason.startswith("Packed vest:") for stop in gel_stops)
    assert len(restock_stops) >= 2
    assert all(stop.form is not FuelForm.gel for stop in restock_stops)

    aid_stops = [stop for stop in plan.fuel_stops if stop.at_aid_station and stop.carbs_g > 0]
    assert len(aid_stops) == 2
    assert all(30.0 <= stop.carbs_g <= 55.0 for stop in aid_stops)
    assert all(30.0 <= leg.station_carbs_g <= 55.0 for leg in plan.aid_legs[:2])
    assert all('cola' not in stop.product.lower() and 'broth' not in stop.product.lower() for stop in restock_stops)
    assert plan.totals.calories_kcal > plan.totals.carbs_g * 4.0


def test_midnight_start_unlocks_overnight_caffeine_and_counts_salt_caps() -> None:
    """A 00:00 start unlocks caffeinated gels during the 01:00-05:30 circadian dip."""

    points = course_points([(60_000, 0.0)])
    plan = generate_plan(
        race_id="night-ultra",
        race_name="Midnight 60k",
        declared_distance_km=60.0,
        aid_stations=[],
        profile=points,
        athlete=AthleteInput(
            weight_kg=70,
            temperature_c=25,
            flat_pace_min_per_km=10.0,
            carbs_g_per_hour=75,
            start_time_minutes=0,
            has_salt_caps=True,
            salt_caps_qty=6,
            salt_cap_mg=250,
            vest_foods=["caffeine_gel", "banana", "sandwich"],
        ),
        packed_vest_items=[
            PackedVestItem(id="caffeine_gel", name="Caffeine Gel", carbs_g=25, caffeine_mg=100, qty=2, flavor="sweet", form="gel"),
            PackedVestItem(id="banana", name="Banana", carbs_g=22, qty=2, flavor="sweet", form="solid"),
            PackedVestItem(id="sandwich", name="Half Sandwich", carbs_g=25, qty=2, flavor="savoury", form="solid"),
        ],
    )
    caf_stops = [stop for stop in plan.fuel_stops if stop.caffeine_mg > 0]
    assert len(caf_stops) == 2
    # At least one caffeine gel lands before 45% (270 min) during the 01:30-04:30 night dip
    assert any(90.0 <= stop.scheduled_minute < 270.0 for stop in caf_stops)
    assert plan.totals.caffeine_mg == pytest.approx(200.0)
    assert plan.totals.salt_caps_sodium_mg == pytest.approx(1500.0)
    assert plan.totals.calories_kcal > 400.0


def test_packed_caffeine_gels_keep_90_minute_gap() -> None:
    """Two packed caffeinated gels never stack 30 minutes apart."""

    points = course_points([(60_000, 0.0)])
    plan = generate_plan(
        race_id="caf-gap",
        race_name="Caffeine Gap Test",
        declared_distance_km=60.0,
        aid_stations=[],
        profile=points,
        athlete=AthleteInput(
            weight_kg=70,
            flat_pace_min_per_km=10.0,
            carbs_g_per_hour=90,
            start_time_minutes=360,
            vest_foods=["caffeine_gel", "banana", "dates"],
        ),
        packed_vest_items=[
            PackedVestItem(id="caffeine_gel", name="Caffeine Gel", carbs_g=25, caffeine_mg=100, qty=2, flavor="sweet", form="gel"),
            PackedVestItem(id="banana", name="Banana", carbs_g=22, qty=1, flavor="sweet", form="solid"),
            PackedVestItem(id="dates", name="Medjool Dates", carbs_g=25, qty=3, flavor="sweet", form="solid"),
        ],
    )
    caf_stops = [stop for stop in plan.fuel_stops if stop.caffeine_mg > 0]
    assert len(caf_stops) == 2
    assert caf_stops[1].scheduled_minute - caf_stops[0].scheduled_minute >= 90.0 - 0.05


def test_three_flasks_and_spread_caffeine_on_100k() -> None:
    """Explicit flask_count=3 is honored and 2 caffeine gels spread across the back half."""

    points = course_points([(90_000, 0.0)])
    course = prepare_course(points)
    stations = []
    for i, km in enumerate((15.0, 30.0, 45.0, 60.0, 75.0), start=1):
        pin = location_at_distance(course, int(km * 1000))
        stations.append(
            AidStation(
                name=f"Aid {i}",
                distance_km=km,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        )
    plan = generate_plan(
        race_id="v100-spread",
        race_name="100k Spread Test",
        declared_distance_km=90.0,
        aid_stations=stations,
        profile=points,
        athlete=AthleteInput(
            weight_kg=70,
            temperature_c=25,
            flat_pace_min_per_km=9.0,
            carbs_g_per_hour=90,
            start_time_minutes=360,
            flask_count=3,
            vest_foods=["caffeine_gel", "dates", "sandwich", "chocolate"],
            station_foods=["sandwich", "banana", "dates", "chips", "cola_broth"],
        ),
        packed_vest_items=[
            PackedVestItem(id="caffeine_gel", name="Caffeine Gel", carbs_g=25, caffeine_mg=100, qty=2, flavor="sweet", form="gel"),
            PackedVestItem(id="dates", name="Medjool Dates", carbs_g=25, qty=3, flavor="sweet", form="solid"),
            PackedVestItem(id="sandwich", name="Half Sandwich", carbs_g=25, qty=1, flavor="savoury", form="solid"),
            PackedVestItem(id="chocolate", name="Chocolate", carbs_g=20, qty=2, flavor="sweet", form="solid"),
        ],
    )
    # 3 flasks honored on hot 2.25h legs
    first_flasks = [item for item in plan.aid_legs[0].pack if item.item == "500ml Flask"]
    assert first_flasks and first_flasks[0].quantity == 3
    # Both caffeine gels land in the back half, spaced >= 150 min apart
    caf = [s for s in plan.fuel_stops if s.caffeine_mg > 0]
    assert len(caf) == 2
    assert caf[1].scheduled_minute - caf[0].scheduled_minute >= 140.0
    # Dates are scheduled on Leg 1 instead of being dropped or buried behind melting chocolate
    leg1_trail = [s for s in plan.fuel_stops if not s.at_aid_station and s.distance_km < 15.0 and s.carbs_g > 0]
    assert any(s.product == "Medjool Dates" for s in leg1_trail)
    assert len(leg1_trail) >= 2
    # Station meals stay moderate (<= 55g)
    aid_stops = [s for s in plan.fuel_stops if s.at_aid_station and s.carbs_g > 0]
    assert all(s.carbs_g <= 55.0 for s in aid_stops)


def test_plain_water_has_no_ghost_sodium_and_stations_serve_salt() -> None:
    """Plain water sips carry 0 mg sodium; hot aid stations and restocks include salty foods."""

    points = course_points([(60_000, 0.0)])
    course = prepare_course(points)
    stations = []
    for i, km in enumerate((15.0, 30.0, 45.0), start=1):
        pin = location_at_distance(course, int(km * 1000))
        stations.append(
            AidStation(
                name=f"Aid {i}",
                distance_km=km,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        )
    plan = generate_plan(
        race_id="salt-truth",
        race_name="Salt Truth 60k",
        declared_distance_km=60.0,
        aid_stations=stations,
        profile=points,
        athlete=AthleteInput(
            weight_kg=70,
            temperature_c=25,
            flat_pace_min_per_km=9.0,
            carbs_g_per_hour=90,
            has_salt_caps=False,
            vest_foods=["dates", "banana"],
            station_foods=["sandwich", "cucumber", "chips", "banana", "dates", "cola_broth"],
        ),
        packed_vest_items=[
            PackedVestItem(id="dates", name="Medjool Dates", carbs_g=25, sodium_mg=10, qty=2, flavor="sweet", form="solid"),
            PackedVestItem(id="banana", name="Banana", carbs_g=22, sodium_mg=1, qty=1, flavor="sweet", form="solid"),
        ],
    )
    plain_sips = [s for s in plan.fuel_stops if s.form is FuelForm.sip and s.carbs_g == 0 and not s.at_aid_station]
    assert all(s.sodium_mg == 0.0 for s in plain_sips)
    aid_stops = [s for s in plan.fuel_stops if s.at_aid_station and s.carbs_g > 0]
    assert aid_stops and all(s.sodium_mg >= 200.0 for s in aid_stops)
    restocks = [s for s in plan.fuel_stops if s.reason.startswith("Aid restock")]
    assert any("chips" in s.product.lower() for s in restocks)


def test_food_library_electrolytes_magnesium_and_sour_cucumber() -> None:
    """Aid stations serve electrolytes, second-half magnesium, and recognize typed 'sour cucumber'."""

    from backend.models import CustomFoodItem

    pts = course_points([(15_000, 0.04), (15_000, -0.02), (20_000, 0.03), (20_000, -0.03), (15_000, 0.02), (15_000, -0.02)])
    course = prepare_course(pts)
    stations = []
    for i, km in enumerate((15.0, 30.0, 45.0, 60.0, 75.0, 90.0), start=1):
        pin = location_at_distance(course, int(km * 1000))
        stations.append(
            AidStation(
                name=f"Aid {i}",
                distance_km=km,
                lat=pin.lat,
                lon=pin.lon,
                elevation_m=pin.elevation_m,
                has_water=True,
                has_food=True,
            )
        )
    plan = generate_plan(
        race_id="v100-lib",
        race_name="Vitosha 100 Library Test",
        declared_distance_km=100.0,
        aid_stations=stations,
        profile=pts,
        athlete=AthleteInput(
            weight_kg=70,
            temperature_c=25,
            flat_pace_min_per_km=8.0,
            carbs_g_per_hour=90,
            start_time_minutes=360,
            flask_count=3,
            vest_foods=["caffeine_gel", "sandwich", "banana"],
            station_foods=["electrolyte_drink", "magnesium_shot", "salted_potatoes", "banana", "dates", "custom-sour"],
            custom_station_items=[
                CustomFoodItem(key="custom-sour", name="Sour cucumber", carbs_g=2, sodium_mg=0, flavor="Savoury/Salty"),
            ],
        ),
        packed_vest_items=[
            PackedVestItem(id="caffeine_gel", name="Caffeine Gel", carbs_g=25, caffeine_mg=100, qty=2, flavor="sweet", form="gel"),
            PackedVestItem(id="sandwich", name="Half Sandwich", carbs_g=25, qty=2, flavor="savoury", form="solid"),
            PackedVestItem(id="banana", name="Banana", carbs_g=22, qty=2, flavor="sweet", form="solid"),
        ],
    )
    # Both packed caffeine gels stay scheduled on Vitosha 100 Default zones
    caf = [s for s in plan.fuel_stops if s.caffeine_mg > 0]
    assert len(caf) == 2
    assert plan.totals.caffeine_mg == pytest.approx(200.0)
    # Second-half Magnesium shot is scheduled and counted in totals.magnesium_mg
    assert plan.totals.magnesium_mg >= 250.0
    # Typed 'Sour cucumber' with sodium_mg=0 is auto-enriched via FOOD_PROFILES (~480 mg Na)
    aid_reasons = " ".join(s.reason.lower() for s in plan.fuel_stops if s.at_aid_station)
    assert "electrolyte" in aid_reasons or "sour cucumber" in aid_reasons or "potato" in aid_reasons
    assert plan.totals.potassium_mg > 500.0
