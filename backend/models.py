"""Pydantic schemas for race files, plan requests, and engine output.

These models are the contract between the HTTP API and the sports-science
code. `extra="forbid"` rejects unexpected fields so a misspelled key fails
loudly instead of being ignored.
"""

from __future__ import annotations

from enum import Enum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

_FUEL_PREFS = {"gels_first", "balanced_real_food", "real_food_heavy"}
_FUEL_ALIASES = {
    "gels": "gels_first",
    "gel": "gels_first",
    "balanced": "balanced_real_food",
    "real_food": "real_food_heavy",
    "real": "real_food_heavy",
}


def _bounded(value: object, default: float, low: float, high: float) -> float:
    """Turn a stale JSON number into a value the field will accept."""

    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:
        return default
    return min(high, max(low, number))


def _clean_locker_item(raw: object) -> dict[str, object] | None:
    """Drop unknown locker keys and map an old `solid` kind onto `bar`."""

    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:60]
    carbs = _bounded(raw.get("carbs_g", raw.get("carbs")), 0, 0, 120)
    if not name or not 0 < carbs <= 120:
        return None
    kind = str(raw.get("kind") or "gel").strip().lower()
    if kind in {"solid", "food", "bar", "real"}:
        kind = "bar"
    elif kind == "liquid":
        kind = "liquid"
    else:
        kind = "gel"
    return {
        "name": name,
        "carbs_g": carbs,
        "caffeine_mg": _bounded(raw.get("caffeine_mg", raw.get("caffeine")), 0, 0, 400),
        "kind": kind,
    }


def _clean_packed_item(raw: object) -> dict[str, object] | None:
    """Accept a vest row saved by an older screen.

    Old rows used `Sweet`, `kind`, and `carbs`. Those become the current
    flavor, form, and carb fields. A row that still has no name is skipped
    instead of rejecting the whole plan.
    """

    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:60]
    item_id = str(raw.get("id") or name).strip()[:40]
    carbs = _bounded(raw.get("carbs_g", raw.get("carbs")), 0, 0, 120)
    if not name or not item_id or not 0 < carbs <= 120:
        return None
    flavor = str(raw.get("flavor") or "sweet").strip().lower()
    if "sav" in flavor or "salt" in flavor:
        flavor = "savoury"
    elif flavor == "neutral":
        flavor = "neutral"
    else:
        flavor = "sweet"
    form = str(raw.get("form") or raw.get("kind") or "gel").strip().lower()
    if form in {"liquid", "drink", "mix"}:
        form = "liquid"
    elif form in {"solid", "bar", "food", "real"}:
        form = "solid"
    else:
        form = "gel"
    try:
        qty = int(float(raw.get("qty", 1)))
    except (TypeError, ValueError):
        qty = 1
    return {
        "id": item_id,
        "name": name,
        "carbs_g": carbs,
        "sodium_mg": _bounded(raw.get("sodium_mg", raw.get("sodium", 50)), 50, 0, 2000),
        "caffeine_mg": _bounded(raw.get("caffeine_mg", raw.get("caffeine")), 0, 0, 400),
        "qty": min(30, max(0, qty)),
        "flavor": flavor,
        "form": form,
    }


def _prepare_plan_body(data: object) -> object:
    """Normalize a plan body before strict field checks run."""

    if not isinstance(data, dict):
        return data
    body = dict(data)
    athlete = body.get("athlete")
    if isinstance(athlete, dict):
        athlete = dict(athlete)
        preference = athlete.get("fuel_preference")
        if isinstance(preference, str) and preference not in _FUEL_PREFS:
            athlete["fuel_preference"] = _FUEL_ALIASES.get(preference, "gels_first")
        locker = athlete.get("locker")
        if isinstance(locker, list):
            athlete["locker"] = [item for item in (_clean_locker_item(raw) for raw in locker) if item][:12]
        elif locker is not None:
            athlete["locker"] = []
        body["athlete"] = athlete
    packed = body.get("packed_vest_items")
    if isinstance(packed, list):
        body["packed_vest_items"] = [item for item in (_clean_packed_item(raw) for raw in packed) if item][:24]
    elif packed is not None:
        body["packed_vest_items"] = []
    return body


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


class PackedVestItem(APIModel):
    """One food the runner actually packed, with the exact piece count.

    The fueling engine spends this list. Empty means the plan keeps the
    usual rotation. `flavor` is sweet, savoury, or neutral. `form` is
    liquid, gel, or solid.
    """

    id: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)
    carbs_g: float = Field(gt=0, le=120)
    sodium_mg: float = Field(default=50.0, ge=0, le=2000)
    caffeine_mg: float = Field(default=0.0, ge=0, le=400)
    qty: int = Field(default=1, ge=0, le=30)
    flavor: str = Field(default="sweet", pattern=r"^(sweet|savoury|neutral)$")
    form: str = Field(default="gel", pattern=r"^(liquid|gel|solid)$")


class CustomFoodItem(APIModel):
    """A renamed portion, or a food that is not in the built-in menu.

    `key` matches a vest or station chip (`chocolate`, `sandwich`, …).
    Leave it blank to add a brand-new food. Empty lists are the default, so
    a plan that never customizes a portion keeps the catalog names and the
    30 g feed windows.
    """

    key: str = Field(default="", max_length=40)
    name: str = Field(min_length=1, max_length=60)
    carbs_g: float = Field(gt=0, le=120)
    sodium_mg: float = Field(default=0, ge=0, le=2000)
    flavor: str = Field(default="Sweet", pattern=r"^(Sweet|Savoury/Salty)$")


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
    vest_foods: list[str] = Field(
        default_factory=lambda: ["gel_25", "drink_30", "bar", "sandwich", "dates"],
        description="Vest foods the plan may rotate. Unknown keys are ignored.",
    )
    station_foods: list[str] = Field(
        default_factory=lambda: ["sandwich", "watermelon", "cucumber", "chips", "cola_broth"],
        description="Foods offered at stations with food. Unknown keys are ignored.",
    )
    custom_vest_items: list[CustomFoodItem] = Field(
        default_factory=list,
        max_length=24,
        description="Renamed vest portions and extra vest foods. Empty keeps the catalog.",
    )
    custom_station_items: list[CustomFoodItem] = Field(
        default_factory=list,
        max_length=24,
        description="Renamed aid-station portions and extra station foods. Empty keeps the 28 g plate.",
    )
    carb_multiplier: float = Field(
        default=1.0,
        ge=0.75,
        le=1.25,
        description="Scales the hourly carb target after a gut quiz. 1.0 leaves it unchanged.",
    )
    climb_sensitivity: str = Field(
        default="normal",
        pattern=r"^(normal|sensitive)$",
        description="sensitive moves the green/yellow climb line from +5% to +3.5% when green_max_grade is omitted.",
    )
    green_min_grade: float | None = Field(
        default=None,
        ge=-12,
        le=-3,
        description="Steepest descent that still allows a full gel. Empty uses −8%.",
    )
    green_max_grade: float | None = Field(
        default=None,
        ge=2,
        le=12,
        description="Steepest climb that still allows a full gel. Empty uses +5%, or +3.5% when climb_sensitivity is sensitive.",
    )
    red_descent_grade: float | None = Field(
        default=None,
        ge=-20,
        le=-8,
        description="Descents steeper than this are red. Empty uses −12%.",
    )
    red_climb_grade: float | None = Field(
        default=None,
        ge=6,
        le=20,
        description="Climbs steeper than this are red. Empty uses +10%.",
    )
    early_savoury: bool = Field(
        default=False,
        description="Start savoury foods at 1.5 h instead of 2.5 h.",
    )
    target_time_minutes: float | None = Field(
        default=None,
        ge=30,
        le=48 * 60,
        description="Finish target in minutes. 120 or less uses the short-race protocol.",
    )
    has_salt_caps: bool = Field(
        default=False,
        description="Salt capsules are packed only when the runner turns them on.",
    )
    salt_caps_qty: int = Field(
        default=0,
        ge=0,
        le=40,
        description="How many salt capsules are in the vest. Zero means none are packed.",
    )
    salt_cap_mg: float = Field(default=200, ge=0, le=2000)
    flask_count: int = Field(
        default=2,
        ge=0,
        le=6,
        description="500 ml flasks the runner actually carries.",
    )

    @model_validator(mode="after")
    def zone_lines_keep_a_yellow_band(self) -> AthleteInput:
        """Keep a yellow step on both sides of green, and keep flat ground green.

        Omitted lines are filled from the constants first, including the
        sensitive +3.5% climb line, so an old request still validates.
        """

        from backend.engine.zones import zone_edges_for

        edges = zone_edges_for(self)
        ordered = edges.red_descent + 1 <= edges.green_min < 0 < edges.green_max <= edges.red_climb - 1
        if not ordered:
            raise ValueError(
                "Zone lines need a yellow band: red descent + 1 ≤ full-gel floor < 0 < full-gel ceiling ≤ red climb − 1"
            )
        return self


class CustomAidInput(APIModel):
    """An aid station typed by the runner, before it is snapped to the track.

    `distance_km` is the kilometer they mean. The engine moves it to the
    nearest GPX trackpoint and fills in latitude, longitude, and elevation.
    """

    name: str = Field(min_length=1, max_length=80)
    distance_km: float = Field(ge=0, le=500)
    has_water: bool = True
    has_food: bool = False
    drop_bag: bool = False
    cutoff_hours: float | None = Field(default=None, gt=0, le=72)


# The largest bundled GPX is about 3.2 MB. This cap leaves headroom and rejects a huge upload.
GPX_XML_MAX_CHARS = 8_000_000


class GeneratePlanRequest(APIModel):
    """Body for `POST /api/generate-plan`.

    Send a stored `race_id` or a raw GPX document, not both.
    `aid_stations` is optional. A GPX upload has no catalog stations, so this
    list is the whole resupply plan. On a stored race these are added on top.
    """

    race_id: str | None = None
    gpx_xml: str | None = Field(
        default=None,
        max_length=GPX_XML_MAX_CHARS,
        description="Raw GPX document. Longer than 8,000,000 characters is rejected.",
    )
    athlete: AthleteInput = Field(default_factory=AthleteInput)
    aid_stations: list[CustomAidInput] = Field(default_factory=list, max_length=40)
    packed_vest_items: list[PackedVestItem] = Field(
        default_factory=list,
        max_length=24,
        description="Exact vest inventory. Empty keeps the usual food rotation.",
    )
    match_target_time: bool = Field(
        default=False,
        description=(
            "When true, set flat pace from target_time_minutes using the segment "
            "clock, then build the fuel plan once. Off, the sent pace is kept."
        ),
    )
    gpx_track: str = Field(
        default="longest",
        max_length=20,
        description="Uploaded GPX track index, 'longest', or 'merge'. Catalog races ignore this.",
    )
    distance_override_km: float | None = Field(
        default=None,
        gt=0,
        le=500,
        description="When set on an upload, stretch the GPX distance to this many kilometres.",
    )

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_vest(cls, data: object) -> object:
        return _prepare_plan_body(data)

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
    gpx_xml: str | None = Field(
        default=None,
        max_length=GPX_XML_MAX_CHARS,
        description="Raw GPX document. Longer than 8,000,000 characters is rejected.",
    )
    athlete: AthleteInput = Field(default_factory=AthleteInput)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    elapsed_min: float = Field(ge=0, lt=60 * 48)
    # `gi_distress` is the same switch. The live app sends `gi_rescue`.
    gi_rescue: bool = Field(default=False, validation_alias=AliasChoices("gi_rescue", "gi_distress"))
    aid_stations: list[CustomAidInput] = Field(default_factory=list, max_length=40)
    packed_vest_items: list[PackedVestItem] = Field(
        default_factory=list,
        max_length=24,
        description="Exact vest inventory. Empty keeps the usual food rotation.",
    )
    gpx_track: str = Field(default="longest", max_length=20)
    distance_override_km: float | None = Field(default=None, gt=0, le=500)

    @model_validator(mode="before")
    @classmethod
    def accept_legacy_vest(cls, data: object) -> object:
        return _prepare_plan_body(data)

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


class ReasonCode(str, Enum):
    """Why a stop moved, or why its carbs are not the full window.

    One code per scheduler branch. The words live in the frontend.
    """

    SHIFTED_EARLIER = "SHIFTED_EARLIER"
    SHIFTED_LATER = "SHIFTED_LATER"
    YELLOW_HALF = "YELLOW_HALF"
    SKIPPED_RED_ZONE = "SKIPPED_RED_ZONE"
    GI_RESCUE_NIL = "GI_RESCUE_NIL"
    GI_RESCUE_EASE = "GI_RESCUE_EASE"
    GI_RESCUE_RED = "GI_RESCUE_RED"
    GI_RESCUE_HALF = "GI_RESCUE_HALF"
    QUIET_WINDOW = "QUIET_WINDOW"
    DESCENT_LOCK = "DESCENT_LOCK"
    AID_RESTOCK = "AID_RESTOCK"
    AID_COVERED = "AID_COVERED"
    OPENING_SOLID = "OPENING_SOLID"
    SOLID_SLOT = "SOLID_SLOT"
    SAVOURY_SLOT = "SAVOURY_SLOT"
    PORTION = "PORTION"
    NO_VEST_FOOD = "NO_VEST_FOOD"
    NO_CLIMB_FOOD = "NO_CLIMB_FOOD"
    PACKED_VEST = "PACKED_VEST"
    SHORT_RACE_BITE = "SHORT_RACE_BITE"
    SHORT_RACE_SIP = "SHORT_RACE_SIP"
    UNPLACED = "UNPLACED"


class StopReason(APIModel):
    """One structured reason on a stop. `params` are numbers, never sentences."""

    code: ReasonCode
    params: dict[str, float] = Field(default_factory=dict)


class ForfeitedDose(APIModel):
    """Carbs a window calculated and then did not leave on a stop.

    `carbs_g` is always above zero. A 0 g rescue sip is not a forfeited row.
    """

    at_min: float
    carbs_g: float = Field(gt=0)
    code: ReasonCode
    params: dict[str, float] = Field(default_factory=dict)


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
    reasons: list[StopReason] = Field(default_factory=list)


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
    carryover_carbs_g: float = Field(
        default=0,
        ge=0,
        description="Carbs already eaten that are still digesting on this leg.",
    )
    pack: list[PackItem]
    note: str = ""


class DeliveryTier(str, Enum):
    """How the delivered rate sits against the hourly target.

    `warn` is under 70%. `info` is from 70% up to 85%. `ok` is 85% and above.
    A short race is `ok` even when the rate is low. The warning sentence stays
    on `RacePlan.warnings`. The info sentence is `FuelBudget.note`.
    """

    ok = "ok"
    info = "info"
    warn = "warn"


class FuelBudget(APIModel):
    """Delivered carbs against the hourly target. Scheduling is unchanged.

    Rates use the same window as `PlanTotals.carbs_g_per_hour_delivered`: carbs
    on stops at or after the pre-race quiet minute, divided by the hours from
    that minute to the finish. `forfeited_total_g` is the forfeited list, which
    is not the gap between the target and what was delivered.
    """

    target_g_per_h: float = Field(default=0, ge=0)
    delivered_g_per_h: float = Field(default=0, ge=0)
    delivery_ratio: float = Field(default=0, ge=0)
    target_total_g: float = Field(default=0, ge=0)
    delivered_total_g: float = Field(default=0, ge=0)
    forfeited_total_g: float = Field(default=0, ge=0)
    forfeited_by_code: dict[str, float] = Field(default_factory=dict)
    red_share_of_course_pct: float = Field(default=0, ge=0, le=100)
    red_share_of_time_pct: float = Field(default=0, ge=0, le=100)
    tier: DeliveryTier = DeliveryTier.ok
    note: str = ""


class ZonePreview(APIModel):
    """Distance in each digestive zone. This is not a fueling plan."""

    distance_m: float = Field(ge=0)
    red_distance_m: float = Field(ge=0)
    red_share_of_course_pct: float = Field(ge=0, le=100)
    zone_counts: dict[str, int]
    zone_distance_m: dict[str, float]


class PlanTotals(APIModel):
    """Sums of the scheduled stops.

    `carbs_g_per_kg_per_hour` is the athlete's target rate (grams per kilogram
    per hour), not a sum of the stops. `carbs_g_per_hour_delivered` is the
    carbs actually scheduled per hour from the end of the pre-race quiet
    window to the finish.
    """

    carbs_g: float = Field(ge=0)
    fluid_ml: float = Field(ge=0)
    sodium_mg: float = Field(ge=0)
    carbs_g_per_kg_per_hour: float = Field(ge=0)
    carbs_g_per_hour_delivered: float = Field(default=0, ge=0)
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
    forfeited: list[ForfeitedDose] = Field(default_factory=list)
    aid_legs: list[AidLeg]
    aid_stations: list[AidStation] = Field(default_factory=list)
    totals: PlanTotals
    fuel_budget: FuelBudget = Field(default_factory=FuelBudget)
    warnings: list[str]
    vest_inventory_summary: str = ""


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
