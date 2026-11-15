"""Pydantic schemas for race files, plan requests, and engine output.

These models are the contract between the HTTP API and the sports-science
code. `extra="forbid"` rejects unexpected fields so a misspelled key fails
loudly instead of being ignored.
"""

from __future__ import annotations

from enum import Enum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class APIModel(BaseModel):
    """Base model: unknown JSON keys are rejected."""

    model_config = ConfigDict(extra="forbid")


class DigestiveZone(str, Enum):
    """How safe it is to take in dense calories on a segment."""

    green = "green"
    yellow = "yellow"
    red = "red"


class FuelForm(str, Enum):
    """What the runner should actually swallow at a stop."""

    gel = "gel"
    liquid = "liquid"
    micro_dose = "micro_dose"
    real_food = "real_food"
    sip = "sip"
    rescue_sip = "rescue_sip"


class TrackPoint(APIModel):
    """One sample of a race track, before smoothing or slicing."""

    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    elevation_m: float = Field(gt=-500, lt=9000)


class AidStation(APIModel):
    """A resupply point the packing list is built around."""

    name: str = Field(min_length=1)
    distance_km: float = Field(ge=0)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    elevation_m: float = Field(gt=-500, lt=9000)
    cutoff_hours: float | None = Field(default=None, gt=0, le=72)
    has_water: bool = True
    has_food: bool = False
    drop_bag: bool = False
    notes: str = ""


class Race(APIModel):
    """A stored race: metadata, aid stations, and a track.

    The track is either inlined in `profile` or read from `gpx_file` when a
    plan is built. The catalog file keeps the GPX path and does not copy
    every track point into JSON.
    """

    id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1)
    distance_km: float = Field(gt=0, le=500)
    elevation_gain_m: float = Field(ge=0)
    location: str
    summary: str
    profile_source: str = Field(pattern=r"^(synthetic|gpx)$")
    gpx_file: str | None = None
    aid_stations: list[AidStation]
    profile: list[TrackPoint] = Field(default_factory=list)

    @model_validator(mode="after")
    def has_a_track_source(self) -> Race:
        if self.gpx_file or len(self.profile) >= 2:
            return self
        raise ValueError("Race needs a gpx_file or a profile with at least two points")


class RaceCatalog(APIModel):
    """Response body for `GET /api/races`."""

    races: list[Race]


class LockerItem(APIModel):
    """One product the runner actually carries.

    `kind` is gel, liquid, or bar. Caffeine is scheduled only from a gel
    whose `caffeine_mg` is above zero, and the engine never plans more than
    5 mg per kilogram of body weight for the whole race.
    """

    name: str = Field(min_length=1, max_length=60)
    carbs_g: float = Field(gt=0, le=120)
    caffeine_mg: float = Field(default=0, ge=0, le=400)
    kind: str = Field(default="gel", pattern=r"^(gel|liquid|bar)$")


class AthleteInput(APIModel):
    """Personal targets for one race plan.

    Grade Adjusted Pace uses a cost ratio, so body mass does not change split
    times. Weight does change sweat rate, together with `temperature_c`.
    A finished plan overwrites `fluid_ml_per_hour` and `sodium_mg_per_hour`
    with that formula. Carbohydrate rate is the athlete's own gut target.
    """

    weight_kg: float = Field(default=70, gt=30, lt=250)
    temperature_c: float = Field(
        default=18,
        ge=-20,
        le=45,
        description="Air temperature in Celsius. Sets hourly fluid and sodium.",
    )
    flat_pace_min_per_km: float = Field(
        default=8.0,
        gt=3.5,
        lt=20,
        description="Target effort on flat ground, in minutes per kilometer. This is the GAP the plan tries to hold.",
    )
    carbs_g_per_hour: float = Field(default=60, gt=0, le=120)
    fluid_ml_per_hour: float = Field(
        default=500,
        gt=0,
        le=2500,
        description="Replaced by the temperature formula when a plan is built.",
    )
    sodium_mg_per_hour: float = Field(
        default=500,
        ge=0,
        le=2500,
        description="Replaced by the temperature formula when a plan is built.",
    )
    feed_interval_min: int = Field(default=30, ge=15, le=45)
    fuel_preference: str = Field(
        default="gels_first",
        pattern=r"^(gels_first|balanced_real_food|real_food_heavy)$",
        description="gels_first, balanced_real_food, or real_food_heavy.",
    )
    locker: list[LockerItem] = Field(
        default_factory=list,
        max_length=12,
        description="Exact products the runner will carry. Empty keeps the plain 25 g gel.",
    )


class CustomAidInput(APIModel):
    """An aid station typed by the runner, before it is snapped to the track.

    `distance_km` is the kilometer they mean. The engine moves it to the
    nearest GPX trackpoint and fills in latitude, longitude, and elevation.
    """

    name: str = Field(min_length=1, max_length=80)
    distance_km: float = Field(ge=0, le=500)
    has_water: bool = True
    has_food: bool = False
    cutoff_hours: float | None = Field(default=None, gt=0, le=72)


class GeneratePlanRequest(APIModel):
    """Body for `POST /api/generate-plan`.

    Send a stored `race_id` or a raw GPX document, not both.
    `aid_stations` is optional. A GPX upload has no catalog stations, so this
    list is the whole resupply plan. On a stored race these are added on top.
    """

    race_id: str | None = None
    gpx_xml: str | None = None
    athlete: AthleteInput = Field(default_factory=AthleteInput)
    aid_stations: list[CustomAidInput] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def exactly_one_course_source(self) -> GeneratePlanRequest:
        has_race = bool(self.race_id and self.race_id.strip())
        has_gpx = bool(self.gpx_xml and self.gpx_xml.strip())
        if has_race == has_gpx:
            raise ValueError("Provide exactly one of race_id or gpx_xml")
        return self


class RecalibrateRequest(APIModel):
    """Body for `POST /api/recalibrate`.

    `elapsed_min` is the runner's race clock. `gi_rescue` turns on the
    one-tap stomach protocol from this moment forward.
    """

    race_id: str | None = None
    gpx_xml: str | None = None
    athlete: AthleteInput = Field(default_factory=AthleteInput)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    elapsed_min: float = Field(ge=0, lt=60 * 48)
    # `gi_distress` is the same switch. The live app sends `gi_rescue`.
    gi_rescue: bool = Field(default=False, validation_alias=AliasChoices("gi_rescue", "gi_distress"))
    aid_stations: list[CustomAidInput] = Field(default_factory=list, max_length=40)

    @model_validator(mode="after")
    def exactly_one_course_source(self) -> RecalibrateRequest:
        has_race = bool(self.race_id and self.race_id.strip())
        has_gpx = bool(self.gpx_xml and self.gpx_xml.strip())
        if has_race == has_gpx:
            raise ValueError("Provide exactly one of race_id or gpx_xml")
        return self


class Segment(APIModel):
    """A ~500 m slice of the course with pace and digestive-zone data."""

    index: int = Field(ge=0)
    start_distance_m: float = Field(ge=0)
    end_distance_m: float = Field(gt=0)
    distance_m: float = Field(gt=0)
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    elevation_start_m: float
    elevation_end_m: float
    elevation_gain_m: float = Field(ge=0)
    elevation_loss_m: float = Field(ge=0)
    grade_percent: float
    zone: DigestiveZone
    cost_factor: float = Field(gt=0)
    predicted_minutes: float = Field(gt=0)
    start_minute: float = Field(ge=0)
    end_minute: float = Field(gt=0)
    predicted_pace_min_per_km: float = Field(gt=0)
    gap_min_per_km: float = Field(gt=0)


class FuelStop(APIModel):
    """One planned intake, already moved onto the safest nearby terrain."""

    sequence: int = Field(ge=1)
    ideal_minute: float
    scheduled_minute: float
    shift_minutes: float
    distance_km: float = Field(ge=0)
    latitude: float
    longitude: float
    segment_index: int = Field(ge=0)
    grade_percent: float
    zone: DigestiveZone
    carbs_g: float = Field(ge=0)
    fluid_ml: float = Field(ge=0)
    sodium_mg: float = Field(ge=0)
    form: FuelForm
    reason: str
    caffeine_mg: float = Field(default=0, ge=0)
    product: str = ""
    flavor: str = ""
    at_aid_station: bool = False


class PackItem(APIModel):
    """One thing to leave an aid station carrying."""

    item: str
    quantity: int = Field(ge=0)
    detail: str


class AidLeg(APIModel):
    """Nutrition to carry between two resupply points."""

    from_name: str
    to_name: str
    from_km: float = Field(ge=0)
    to_km: float = Field(gt=0)
    distance_km: float = Field(ge=0)
    predicted_minutes: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    fluid_ml: float = Field(ge=0)
    sodium_mg: float = Field(ge=0)
    vest_carbs_g: float = Field(default=0, ge=0)
    station_carbs_g: float = Field(default=0, ge=0)
    station_meal: str = ""
    pack: list[PackItem]
    note: str = ""


class PlanTotals(APIModel):
    """Sums of the scheduled stops.

    `carbs_g_per_kg_per_hour` is the athlete's target rate (grams per kilogram
    per hour), not a sum of the stops.
    """

    carbs_g: float = Field(ge=0)
    fluid_ml: float = Field(ge=0)
    sodium_mg: float = Field(ge=0)
    carbs_g_per_kg_per_hour: float = Field(ge=0)
    stops_on_time: int = Field(ge=0)
    stops_shifted: int = Field(ge=0)
    stops_deferred: int = Field(ge=0)
    caffeine_mg: float = Field(default=0, ge=0)
    caffeine_cap_mg: float = Field(
        default=0,
        ge=0,
        description="Safe ceiling for this athlete: 5 mg of caffeine per kg of body weight.",
    )


class RacePlan(APIModel):
    """Full response for a generated or recalibrated plan."""

    race_id: str
    race_name: str
    athlete: AthleteInput
    gap_model: str
    window_min: int
    declared_distance_km: float
    distance_km: float
    elevation_gain_m: float
    clock_start_min: float
    predicted_duration_min: float
    zone_counts: dict[str, int]
    segments: list[Segment]
    fuel_stops: list[FuelStop]
    aid_legs: list[AidLeg]
    aid_stations: list[AidStation] = Field(default_factory=list)
    totals: PlanTotals
    warnings: list[str]


class SnapResult(APIModel):
    """Where a live GPS fix landed on the race track."""

    latitude: float
    longitude: float
    distance_along_m: float = Field(ge=0)
    distance_along_km: float = Field(ge=0)
    off_course_m: float = Field(ge=0)
    on_course: bool
    segment_index: int = Field(ge=0)
    predicted_minute: float = Field(ge=0)
    elapsed_minute: float = Field(ge=0)
    pace_ratio: float = Field(gt=0)


class RecalibrateResponse(APIModel):
    """Snap result plus the plan for the ground still ahead."""

    snap: SnapResult
    minutes_behind_plan: float
    gi_rescue_applied: bool
    message: str
    remaining: RacePlan
