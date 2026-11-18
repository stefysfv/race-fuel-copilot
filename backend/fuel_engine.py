"""Grade Adjusted Pace, digestive zones, and the fueling schedule.

The product spec names these behaviors. This module is the concrete version:

- GAP uses the Minetti 2002 running-cost polynomial. The athlete's flat
  effort pace is multiplied by C(grade) / C(0) to get time on that slope.
- Digestive zones: green from -8% to +5%, yellow up to +10% or down to -12%,
  and red outside that. A feed may move up to 6 minutes to reach green, then
  yellow. If the whole window is red, that window takes a 15 g liquid
  micro-dose and the rest of its carbs are forfeited. Windows do not share
  a carb debt.
- Aid-station packs, including Start at 0 km, are
  leg hours x hourly target x 1.15.
- Fluid and sodium come from air temperature and body weight.
- Live GPS snaps to the nearest point on the track. Remaining splits are
  stretched by actual_time / predicted_time.
- GI Rescue pauses calories, then eases back with half doses.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

if __name__ == "__main__" and __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.gpx_parser import (
    CoursePoint,
    CourseSegment,
    attach_track,
    haversine_m,
    location_at_distance,
    parse_gpx_xml,
    prepare_course,
    slice_segments,
    smooth_elevation,
)
from backend.models import (
    AidLeg,
    AidStation,
    AthleteInput,
    CustomAidInput,
    DigestiveZone,
    FuelForm,
    FuelStop,
    CustomFoodItem,
    LockerItem,
    PackItem,
    PackedVestItem,
    PlanTotals,
    Race,
    RacePlan,
    RecalibrateResponse,
    Segment,
    SnapResult,
    TrackPoint,
)

# Minetti et al., J Appl Physiol, 2002. Gradient is rise/run, not percent.
# C(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6  (J/kg/m)
FLAT_COST_J_PER_KG_M = 3.6
MIN_COST_J_PER_KG_M = 1.0
MAX_ABS_GRADE_FRACTION = 0.40

# Green is closed on both ends. Yellow uphill starts just above +5% and
# includes +10%. Yellow downhill starts just below -8% and includes -12%.
GREEN_MIN_GRADE = -8.0
GREEN_MAX_GRADE = 5.0
YELLOW_UPHILL_MAX = 10.0
YELLOW_DOWNHILL_MIN = -12.0
WINDOW_MIN = 6.0
PACK_BUFFER = 1.15
GEL_CARBS_G = 25.0
FLASK_ML = 500.0
CAPSULE_SODIUM_MG = 200.0
# Steep ground cannot take a full gel. 15 g is the most this window will feed.
RED_MICRO_DOSE_G = 15.0
# A caffeinated gel is placed before a steep climb, or once the clock is
# past halfway, and the running total stops at 5 mg per kg of body weight.
CAFFEINE_MG_PER_KG = 5.0
CLIMB_LOOKAHEAD_MIN = 20.0
MAJOR_CLIMB_GRADE = 8.0
# After 3.5 hours, green feeds in the second half leave the sweet gels behind.
PALATE_FATIGUE_MIN = 210.0
SAVOURY_START_MIN = 150.0
EARLY_SAVOURY_MIN = 90.0
# The first 90 minutes stay on gels and drink mix. A station plate is only
# worth sitting down for when the leg that arrives there lasted 40 minutes.
OPENING_EASY_MIN = 90.0
SOLID_EVERY_MIN = 90.0
AID_LEG_MIN_MINUTES = 40.0
# A race of two hours or less still has glycogen. One bottle and one small bite.
SHORT_RACE_MAX_MIN = 120.0
SHORT_RACE_NOTE = (
    "Short Race Protocol: Rely on pre-race carb-loading (eat 2h before). "
    "Minimal mid-race fueling required."
)
GEL_VEST_KEYS = frozenset({"gel_25", "iso_gel_22", "high_carb_gel", "caffeine_gel"})
# The pre-race meal covers the opening. Trail gels and solids wait.
PRE_RACE_QUIET_MIN = 50.0
PRE_RACE_SHORT_LEG_MIN = 75.0
PRE_RACE_FOOD_STATION_MIN = 100.0
CARRYOVER_WINDOW_MIN = 55.0
DESCENT_LOCK_GRADE = -3.5
SOLID_LAG_MIN = 40.0
SOLID_CLEAR_CLIMB = 9.0
SOLID_CLEAR_DESCENT = -5.0
FLASK_CAP = 2
FLASK_CAP_LONG = 3
LONG_LEG_HOURS = 2.5
# A pin in the last 500 m is the finish, not a one-minute extra leg.
FINISH_MERGE_M = 500.0
SENSITIVE_GREEN_MAX = 3.5
AID_BITE_CARBS_G = 28.0
FLAT_GREEN_GRADE = 3.0
AID_PLATE = "Aid Station Plate: 2 pieces banana / salted potatoes / orange wedges + broth/cola"

# GI Rescue: 20 min with no calories, then small liquid carbs until 45 min,
# then two half-size feeds. Each of those feeds stands alone.
RESCUE_NIL_CARB_MIN = 20.0
RESCUE_COOLDOWN_MIN = 45.0
RESCUE_SIP_INTERVAL_MIN = 10.0
RESCUE_SIP_FLUID_ML = 80.0
RESCUE_SIP_SODIUM_MG = 150.0
RESCUE_EASE_CARBS_G = 12.0
RESCUE_EASE_FLUID_ML = 120.0
RESCUE_EASE_SODIUM_MG = 200.0
RESCUE_GENTLE_FEEDS = 2

# Below a few minutes of evidence, a GPS fix near the start must not rewrite the race.
MIN_PACE_SAMPLE_MIN = 5.0
MIN_PACE_RATIO = 0.6
MAX_PACE_RATIO = 1.8
OFF_COURSE_M = 75.0
GAP_MODEL_NAME = "minetti_2002"

METERS_PER_DEG_LAT = 111_320.0


@dataclass(frozen=True)
class _MenuItem:
    """One vest or aid-station food the runner can tick in setup."""

    key: str
    name: str
    carbs_g: float
    sodium_mg: float
    flavor: str
    kind: str  # gel, liquid, or solid
    uses_portion: bool = False  # True when the runner set this name and carb count


VEST_MENU: dict[str, _MenuItem] = {
    "gel_25": _MenuItem("gel_25", "Standard Gel", 25, 50, "Sweet", "gel"),
    "iso_gel_22": _MenuItem("iso_gel_22", "Isotonic Gel", 22, 40, "Sweet", "gel"),
    "drink_30": _MenuItem("drink_30", "Drink Mix", 30, 250, "Sweet", "liquid"),
    "high_carb_gel": _MenuItem("high_carb_gel", "High-Carb Gel", 40, 80, "Sweet", "gel"),
    "caffeine_gel": _MenuItem("caffeine_gel", "Caffeine Gel", 25, 50, "Sweet", "gel"),
    "sandwich": _MenuItem("sandwich", "Half Sandwich", 25, 140, "Savoury/Salty", "solid"),
    "bar": _MenuItem("bar", "Oat Bar", 30, 100, "Sweet", "solid"),
    "dates": _MenuItem("dates", "Medjool Dates", 25, 10, "Sweet", "solid"),
    "chocolate": _MenuItem("chocolate", "Chocolate", 20, 20, "Sweet", "solid"),
    "chips": _MenuItem("chips", "Salted Chips", 20, 320, "Savoury/Salty", "solid"),
}
STATION_MENU: dict[str, _MenuItem] = {
    "sandwich": _MenuItem("sandwich", "Sandwich quarters", 30, 250, "Savoury/Salty", "solid"),
    "watermelon": _MenuItem("watermelon", "Watermelon", 12, 5, "Sweet", "solid"),
    "cucumber": _MenuItem("cucumber", "Cucumber", 3, 300, "Savoury/Salty", "solid"),
    "chips": _MenuItem("chips", "Salted chips", 22, 300, "Savoury/Salty", "solid"),
    "chocolate": _MenuItem("chocolate", "Chocolate pieces", 24, 20, "Sweet", "solid"),
    "apple": _MenuItem("apple", "Apple wedges", 18, 2, "Sweet", "solid"),
    "dates": _MenuItem("dates", "Pitted dates", 28, 5, "Sweet", "solid"),
    "banana": _MenuItem("banana", "Banana pieces", 22, 1, "Sweet", "solid"),
    "cola_broth": _MenuItem("cola_broth", "Cola or warm broth", 18, 200, "Sweet", "liquid"),
}
DEFAULT_STATION_KEYS = ("sandwich", "watermelon", "cucumber", "chips", "cola_broth")
SAVOURY_VEST_KEYS = frozenset({"sandwich", "chips"})
HEAVY_VEST_KEYS = frozenset({"sandwich", "chocolate", "chips"})
FRUIT_STATION_KEYS = frozenset({"watermelon", "apple", "banana"})
SALT_STATION_KEYS = frozenset({"cucumber", "chips"})


@dataclass
class _MenuClock:
    """Remembers the last solid so a leg cannot turn into a picnic."""

    last_solid_min: float = -1_000.0
    opening_solid: bool = False


def _menu_items(keys: list[str], catalog: dict[str, _MenuItem]) -> list[_MenuItem]:
    """Keep catalog foods in the order the runner selected them."""

    return [catalog[key] for key in keys if key in catalog]


def _kind_for_custom(name: str) -> str:
    """Guess gel, liquid, or solid from a name the runner typed."""

    lowered = name.casefold()
    if "gel" in lowered:
        return "gel"
    if any(word in lowered for word in ("drink", "cola", "broth", "mix", "flask")):
        return "liquid"
    return "solid"


def _overlay_menu_item(base: _MenuItem, custom: CustomFoodItem) -> _MenuItem:
    """Replace the catalog name and carb grams with the runner's portion.

    Sodium stays on the catalog value when the custom row sends 0, because
    the setup screen edits the label and the carbs, not the salt.
    """

    sodium = custom.sodium_mg if custom.sodium_mg > 0 else base.sodium_mg
    return _MenuItem(
        base.key,
        custom.name.strip() or base.name,
        custom.carbs_g,
        sodium,
        custom.flavor or base.flavor,
        base.kind,
        True,
    )


def _resolved_foods(
    keys: list[str],
    catalog: dict[str, _MenuItem],
    customs: list[CustomFoodItem],
) -> list[_MenuItem]:
    """Catalog chips, plus renames and extra foods the runner typed.

    A custom item whose `key` is in the catalog replaces that chip. Any other
    item is an extra food, included only when the runner still has it selected.
    An empty custom list leaves every chip on its catalog name and grams, and
    `uses_portion` stays false so feed windows remain 30 g.
    """

    overrides: dict[str, CustomFoodItem] = {}
    extras: list[CustomFoodItem] = []
    for item in customs:
        key = item.key.strip()
        if key in catalog and key not in overrides:
            overrides[key] = item
        else:
            extras.append(item)

    resolved: list[_MenuItem] = []
    seen: set[str] = set()
    for base in _menu_items(keys, catalog):
        seen.add(base.key)
        custom = overrides.get(base.key)
        resolved.append(_overlay_menu_item(base, custom) if custom else base)

    selected = set(keys)
    for index, extra in enumerate(extras):
        key = extra.key.strip() or f"custom-{index}"
        if key in seen:
            continue
        if extra.key.strip() and extra.key.strip() not in selected:
            continue
        seen.add(key)
        resolved.append(
            _MenuItem(
                key,
                extra.name.strip(),
                extra.carbs_g,
                extra.sodium_mg,
                extra.flavor,
                _kind_for_custom(extra.name),
                True,
            )
        )
    return resolved


def _gels_selected(athlete: AthleteInput) -> bool:
    """True only when the runner actually ticked a gel.

    An empty vest, or a vest of dates and drink mix, must not fall back to a
    Standard Gel. The check is the two gel keys, not the word "gel" in a note.
    """

    return any(key in athlete.vest_foods for key in GEL_VEST_KEYS)


def _is_short_race(athlete: AthleteInput) -> bool:
    """Glycogen lasts a race of two hours or less. The target clock decides."""

    target = athlete.target_time_minutes
    return target is not None and target <= SHORT_RACE_MAX_MIN


def _green_max_for(athlete: AthleteInput) -> float:
    """Sensitive guts lose solid-food green once the climb passes +3.5%."""

    if athlete.climb_sensitivity == "sensitive":
        return SENSITIVE_GREEN_MAX
    return GREEN_MAX_GRADE


def _scaled_athlete(athlete: AthleteInput) -> AthleteInput:
    """Apply the gut quiz carb multiplier. 1.0 leaves the typed target alone."""

    factor = min(1.25, max(0.75, athlete.carb_multiplier))
    if abs(factor - 1.0) < 1e-9:
        return athlete
    return athlete.model_copy(update={"carbs_g_per_hour": athlete.carbs_g_per_hour * factor})


def station_plate(athlete: AthleteInput) -> tuple[float, str, float]:
    """Carbs, label, and sodium for one aid-station plate.

    The untouched default menu stays at 28 g so the palate check keeps its
    vest split. Any other menu uses the foods the runner actually ticked.
    Watermelon is 12 g and cucumber is a 3 g salt bite, so that plate is
    15 g with the grams written once.
    """

    chosen = _resolved_foods(athlete.station_foods, STATION_MENU, athlete.custom_station_items)
    customized = bool(athlete.custom_station_items)
    if not customized and (len(chosen) < 2 or tuple(athlete.station_foods) == DEFAULT_STATION_KEYS):
        return AID_BITE_CARBS_G, AID_PLATE, 200.0
    if len(chosen) == 1:
        item = chosen[0]
        return item.carbs_g, f"{item.name} ({item.carbs_g:.0f}g)", item.sodium_mg
    if len(chosen) < 2:
        return AID_BITE_CARBS_G, AID_PLATE, 200.0
    fruit = next((item for item in chosen if item.key in FRUIT_STATION_KEYS), None)
    salt = next((item for item in chosen if item.key in SALT_STATION_KEYS), None)
    if fruit is not None and salt is not None:
        first, second = fruit, salt
    else:
        best: tuple[float, _MenuItem, _MenuItem] | None = None
        for index, first in enumerate(chosen):
            for second in chosen[index + 1 :]:
                total = first.carbs_g + second.carbs_g
                if best is None or total < best[0]:
                    best = (total, first, second)
        assert best is not None
        _, first, second = best
    carbs = min(120.0, max(1.0, first.carbs_g + second.carbs_g))
    return carbs, f"{first.name} + {second.name} ({carbs:.0f}g)", first.sodium_mg + second.sodium_mg


@dataclass(frozen=True)
class _Target:
    minute: float
    kind: str  # "normal", "rescue_nil", or "rescue_ease"


@dataclass
class _Dose:
    carbs_g: float
    fluid_ml: float
    sodium_mg: float
    form: FuelForm
    gentle_left: int
    reason: str


def metabolic_cost_j_per_kg_m(grade_fraction: float) -> float:
    """Energy cost of running one metre at this gradient, from Minetti 2002."""

    grade = min(MAX_ABS_GRADE_FRACTION, max(-MAX_ABS_GRADE_FRACTION, grade_fraction))
    cost = (
        155.4 * grade**5
        - 30.4 * grade**4
        - 43.3 * grade**3
        + 46.3 * grade**2
        + 19.5 * grade
        + FLAT_COST_J_PER_KG_M
    )
    return max(cost, MIN_COST_J_PER_KG_M)


def gap_cost_factor(grade_percent: float) -> float:
    """How many times longer this slope takes versus flat ground, at the same effort.

    factor = C(grade) / C(0). Uphill is above 1, mild downhill is below 1.
    """

    return metabolic_cost_j_per_kg_m(grade_percent / 100.0) / FLAT_COST_J_PER_KG_M


def classify_zone(grade_percent: float, green_max: float = GREEN_MAX_GRADE) -> DigestiveZone:
    """Map net segment grade to a digestive safety zone.

    Green:  -8% <= grade <= green_max (default +5%). A gel is reasonable here.
    Yellow: above that line through +10%, or -12% <= grade < -8%. Liquids and a half dose.
    Red:    grade > +10% or grade < -12%. No full gel. A 15 g micro-dose at most.
    A sensitive gut passes green_max=3.5 so moderate climbs leave the green zone.
    """

    if GREEN_MIN_GRADE <= grade_percent <= green_max:
        return DigestiveZone.green
    uphill_yellow = green_max < grade_percent <= YELLOW_UPHILL_MAX
    downhill_yellow = YELLOW_DOWNHILL_MIN <= grade_percent < GREEN_MIN_GRADE
    if uphill_yellow or downhill_yellow:
        return DigestiveZone.yellow
    return DigestiveZone.red


def sweat_rates(temperature_c: float, weight_kg: float) -> tuple[float, float]:
    """Hourly fluid (ml) and sodium (mg) from air temperature and body weight.

    Cool (<15°C):       450 + 3 * (kg - 65) ml/h, 500 mg sodium per litre.
    Moderate (15–25°C): 650 + 4 * (kg - 65) ml/h, 700 mg/L.
    Hot (>25°C):        850 + 5 * (kg - 65) ml/h, 950 mg/L.
    """

    mass_delta = weight_kg - 65.0
    if temperature_c < 15.0:
        ml_per_hour = 450.0 + 3.0 * mass_delta
        mg_per_liter = 500.0
    elif temperature_c <= 25.0:
        ml_per_hour = 650.0 + 4.0 * mass_delta
        mg_per_liter = 700.0
    else:
        ml_per_hour = 850.0 + 5.0 * mass_delta
        mg_per_liter = 950.0
    return ml_per_hour, mg_per_liter * ml_per_hour / 1000.0


def _with_sweat_rates(athlete: AthleteInput) -> AthleteInput:
    """Copy the athlete with fluid and sodium set by the temperature formula."""

    ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    return athlete.model_copy(
        update={
            "fluid_ml_per_hour": ml_per_hour,
            "sodium_mg_per_hour": sodium_per_hour,
        }
    )


def build_timed_segments(
    course_segments: list[CourseSegment],
    athlete: AthleteInput,
    pace_ratio: float = 1.0,
) -> list[Segment]:
    """Attach zone, GAP cost, and a race-clock time to every slice.

    predicted_minutes = kilometres * flat_pace * cost_factor * pace_ratio.
    `gap_min_per_km` is the flat pace that matches this effort. On a climb the
    watch pace (`predicted_pace_min_per_km`) is slower than that GAP.
    """

    if pace_ratio <= 0:
        raise ValueError("pace_ratio must be positive")

    clock_min = 0.0
    green_max = _green_max_for(athlete)
    timed: list[Segment] = []
    for segment in course_segments:
        factor = gap_cost_factor(segment.grade_percent)
        distance_km = segment.distance_m / 1000.0
        minutes = distance_km * athlete.flat_pace_min_per_km * factor * pace_ratio
        minutes = max(minutes, 0.01)
        start_minute = clock_min
        clock_min += minutes
        predicted_pace = minutes / distance_km
        timed.append(
            Segment(
                index=segment.index,
                start_distance_m=segment.start_distance_m,
                end_distance_m=segment.end_distance_m,
                distance_m=segment.distance_m,
                start_lat=segment.start_lat,
                start_lon=segment.start_lon,
                end_lat=segment.end_lat,
                end_lon=segment.end_lon,
                elevation_start_m=segment.elevation_start_m,
                elevation_end_m=segment.elevation_end_m,
                elevation_gain_m=segment.elevation_gain_m,
                elevation_loss_m=segment.elevation_loss_m,
                grade_percent=segment.grade_percent,
                zone=classify_zone(segment.grade_percent, green_max),
                cost_factor=factor,
                predicted_minutes=minutes,
                start_minute=start_minute,
                end_minute=clock_min,
                predicted_pace_min_per_km=predicted_pace,
                gap_min_per_km=predicted_pace / factor,
            )
        )
    return timed


@dataclass(frozen=True)
class _ShortBite:
    """The single mid-race bite for a race of two hours or less."""

    name: str
    carbs_g: float
    form: FuelForm
    detail: str
    flavor: str


def _short_race_bite(athlete: AthleteInput) -> _ShortBite | None:
    """One gel, or one dates/chews portion. Never a stack of both.

    Gels are used only when the runner left one ticked. Otherwise the bite is
    dates, chocolate, a bar, or drink mix, capped around 30 g.
    """

    foods = _resolved_foods(athlete.vest_foods, VEST_MENU, athlete.custom_vest_items)
    by_key = {item.key: item for item in foods if item.key}
    if _gels_selected(athlete):
        item = by_key.get("gel_25") or by_key.get("iso_gel_22") or VEST_MENU["gel_25"]
        carbs = min(30.0, item.carbs_g)
        return _ShortBite(
            item.name,
            carbs,
            FuelForm.gel,
            f"The only mid-race bite ({carbs:.0f} g). Eat it once, near halfway.",
            item.flavor,
        )
    for key in ("dates", "chocolate", "bar"):
        item = by_key.get(key)
        if item is None:
            continue
        carbs = min(30.0, item.carbs_g)
        return _ShortBite(
            item.name,
            carbs,
            FuelForm.real_food,
            f"The only mid-race bite ({carbs:.0f} g). Take tiny bites.",
            item.flavor,
        )
    drink = by_key.get("drink_30")
    if drink is not None:
        carbs = min(30.0, drink.carbs_g)
        return _ShortBite(
            drink.name,
            carbs,
            FuelForm.liquid,
            f"1× {drink.name} ({carbs:.0f}g) in the flask. No extra food.",
            drink.flavor,
        )
    solids = [item for item in foods if item.kind == "solid"]
    if not solids:
        return None
    item = solids[0]
    carbs = min(30.0, item.carbs_g)
    return _ShortBite(
        item.name,
        carbs,
        FuelForm.real_food,
        f"The only mid-race bite ({carbs:.0f} g). Take tiny bites.",
        item.flavor,
    )


HYDRATION_SIP_MIN = 25.0
SHORT_RACE_SIP = "Hydration check · Take 3–4 sips from flask"


def _stop_on_clock(
    segments: list[Segment],
    course: list[CoursePoint],
    ideal: float,
    *,
    prefer_safe: bool,
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    form: FuelForm,
    reason: str,
    product: str,
    flavor: str,
) -> FuelStop | None:
    """One stop near `ideal`, staying inside the terrain window when it can."""

    if not segments:
        return None
    placed = _place_feed(segments, ideal, prefer_safe=prefer_safe)
    if placed is None:
        segment = min(segments, key=lambda item: abs(item.start_minute - ideal))
        when = min(max(ideal, segment.start_minute), segment.end_minute)
    else:
        segment, when = placed
    distance_m = _distance_at_minute(segment, when)
    location = location_at_distance(course, distance_m)
    return FuelStop(
        sequence=1,
        ideal_minute=ideal,
        scheduled_minute=when,
        shift_minutes=when - ideal,
        distance_km=distance_m / 1000.0,
        latitude=location.lat,
        longitude=location.lon,
        segment_index=segment.index,
        grade_percent=segment.grade_percent,
        zone=segment.zone,
        carbs_g=carbs_g,
        fluid_ml=fluid_ml,
        sodium_mg=sodium_mg,
        form=form,
        reason=reason,
        product=product,
        flavor=flavor,
    )


def _short_race_stops(
    segments: list[Segment],
    course: list[CoursePoint],
    athlete: AthleteInput,
    bite: _ShortBite | None,
) -> list[FuelStop]:
    """One bite at 55% plus a sip reminder every 25 minutes.

    Races under 65 minutes keep a single halfway bite and no sip loop.
    Nothing is plated at an aid station.
    """

    if not segments:
        return []
    start = segments[0].start_minute
    duration = segments[-1].end_minute - start
    if duration <= 1:
        return []
    target = athlete.target_time_minutes or 0
    stops: list[FuelStop] = []
    feed_at: float | None = None
    if bite is not None:
        if target >= 65:
            feed_at = start + duration * 0.55
        elif duration <= 20:
            feed_at = start + max(5.0, duration * 0.5)
        else:
            feed_at = start + min(max(20.0, duration * 0.45), duration - 5)
        feed = _stop_on_clock(
            segments,
            course,
            feed_at,
            prefer_safe=True,
            carbs_g=bite.carbs_g,
            fluid_ml=120.0,
            sodium_mg=80.0,
            form=bite.form,
            reason=f"{SHORT_RACE_NOTE} {bite.detail}",
            product=bite.name,
            flavor=bite.flavor,
        )
        if feed is not None:
            stops.append(feed)
            feed_at = feed.scheduled_minute
    if target >= 65:
        minute = start + HYDRATION_SIP_MIN
        while minute < start + duration - 5:
            if feed_at is None or abs(minute - feed_at) >= 10:
                sip = _stop_on_clock(
                    segments,
                    course,
                    minute,
                    prefer_safe=False,
                    carbs_g=0.0,
                    fluid_ml=0.0,
                    sodium_mg=0.0,
                    form=FuelForm.sip,
                    reason=SHORT_RACE_SIP,
                    product="Flask",
                    flavor="",
                )
                if sip is not None:
                    stops.append(sip)
            minute += HYDRATION_SIP_MIN
    stops.sort(key=lambda stop: (stop.scheduled_minute, stop.carbs_g == 0))
    numbered: list[FuelStop] = []
    for sequence, stop in enumerate(stops, start=1):
        numbered.append(stop.model_copy(update={"sequence": sequence}))
    return numbered


def _limit_short_race(legs: list[AidLeg], athlete: AthleteInput, bite: _ShortBite | None) -> list[AidLeg]:
    """Cap the whole race at one bottle (two in the heat) and one bite.

    Later legs carry nothing new. The aid-station table stays empty.
    """

    flasks = 2 if athlete.temperature_c > 25 else 1
    flask_detail = "Water for the whole race."
    if bite is not None and bite.form is FuelForm.liquid:
        flask_detail = bite.detail
    limited: list[AidLeg] = []
    for index, leg in enumerate(legs):
        if index == 0:
            pack = [PackItem(item="500ml Flask", quantity=flasks, detail=flask_detail)]
            carbs = 0.0
            if bite is not None and bite.form is not FuelForm.liquid:
                pack.append(PackItem(item=bite.name, quantity=1, detail=bite.detail))
                carbs = bite.carbs_g
            elif bite is not None:
                carbs = bite.carbs_g
            fluid = flasks * FLASK_ML
            sodium = 200.0
        else:
            pack = []
            carbs = 0.0
            fluid = 0.0
            sodium = 0.0
        limited.append(
            leg.model_copy(
                update={
                    "pack": pack,
                    "carbs_g": carbs,
                    "fluid_ml": fluid,
                    "sodium_mg": sodium,
                    "vest_carbs_g": carbs,
                    "station_carbs_g": 0.0,
                    "station_meal": "",
                    "note": SHORT_RACE_NOTE if index == 0 else "",
                }
            )
        )
    return limited


# A solid on green, or a gel/liquid on yellow and red, gets this added to
# its pick score. 1.0 is enough to break a tie, and smaller than the 1.5
# repeat penalty, so the vest still alternates.
_ZONE_FIT_BONUS = 1.0
_REPEAT_PENALTY = 1.5


@dataclass
class _VestStock:
    """One packed food and how many pieces are still in the vest."""

    item: PackedVestItem
    initial: int
    remaining: int


def _zone_fit_bonus(item: PackedVestItem, zone: DigestiveZone) -> float:
    """Prefer real food on runnable ground, and gels or drink on steep ground."""

    if zone == DigestiveZone.green and item.form == "solid":
        return _ZONE_FIT_BONUS
    if zone in {DigestiveZone.yellow, DigestiveZone.red} and item.form in {"gel", "liquid"}:
        return _ZONE_FIT_BONUS
    return 0.0


def _packed_form(item: PackedVestItem) -> FuelForm:
    if item.form == "liquid":
        return FuelForm.liquid
    if item.form == "solid":
        return FuelForm.real_food
    return FuelForm.gel


def _is_vest_slot(stop: FuelStop) -> bool:
    """A trail feed that can spend one packed piece. Station plates stay put."""

    if stop.at_aid_station or stop.carbs_g <= 0:
        return False
    return stop.form not in {FuelForm.sip, FuelForm.rescue_sip}


def _choose_packed(
    stocks: list[_VestStock],
    zone: DigestiveZone,
    last_id: str | None,
    used_in_leg: dict[str, int],
    placed_carbs: float,
    budget_g: float,
    consume_all: bool,
    blocked_ids: set[str] | None = None,
) -> _VestStock | None:
    """Pick the next piece.

    score = (remaining / initial) * 2 + zone fit - repeat penalty.
    The ratio term drains gel, bar, and sandwich together. The penalty
    blocks a second copy of the item just eaten, and a third copy in the
    same leg, while anything else is still in the vest.
    """

    best: _VestStock | None = None
    best_key: tuple[float, float, int] | None = None
    for stock in stocks:
        if stock.remaining <= 0:
            continue
        if blocked_ids and stock.item.id in blocked_ids:
            continue
        if not consume_all and placed_carbs + stock.item.carbs_g > budget_g + 0.5:
            continue
        ratio = stock.remaining / stock.initial
        others_left = any(
            other.remaining > 0 and other.item.id != stock.item.id for other in stocks
        )
        penalty = 0.0
        if last_id == stock.item.id or (used_in_leg.get(stock.item.id, 0) >= 2 and others_left):
            penalty = _REPEAT_PENALTY
        score = ratio * 2.0 + _zone_fit_bonus(stock.item, zone) - penalty
        key = (score, ratio, -used_in_leg.get(stock.item.id, 0))
        if best_key is None or key > best_key:
            best = stock
            best_key = key
    return best


def _stamp_packed(stop: FuelStop, item: PackedVestItem) -> None:
    stop.product = item.name
    stop.carbs_g = float(item.carbs_g)
    stop.sodium_mg = float(item.sodium_mg)
    stop.caffeine_mg = float(item.caffeine_mg) if item.form == "gel" else 0.0
    stop.form = _packed_form(item)
    stop.flavor = "Savoury/Salty" if item.flavor == "savoury" else "Sweet"
    stop.reason = f"Packed vest: {item.name}"


def _leg_index_for_km(legs: list[AidLeg], distance_km: float) -> int:
    for index, leg in enumerate(legs):
        if distance_km < leg.to_km - 1e-6 or index == len(legs) - 1:
            if distance_km >= leg.from_km - 1e-6:
                return index
    return max(0, len(legs) - 1)


def _keep_pack_line(item: PackItem) -> bool:
    name = item.item.casefold()
    return "flask" in name or "salt" in name


def _inventory_summary(stocks: list[_VestStock]) -> str:
    parts = [
        f"{stock.initial - stock.remaining}/{stock.initial} {stock.item.name}"
        for stock in stocks
    ]
    left = sum(stock.remaining for stock in stocks)
    return f"Vest Inventory Used: {' · '.join(parts)} ({left} left at finish)"


def _consume_packed_vest(
    stops: list[FuelStop],
    legs: list[AidLeg],
    segments: list[Segment],
    course: list[CoursePoint],
    items: list[PackedVestItem],
    feed_interval_min: int,
) -> tuple[str, str]:
    """Spend the packed vest across the race, alternating foods.

    Every piece is scheduled when its carbs fit in the race budget. A vest
    that is bigger than the race is only eaten up to that budget. A short
    race never reaches this function.
    """

    stocks = [
        _VestStock(item=item, initial=item.qty, remaining=item.qty)
        for item in items
        if item.qty > 0
    ]
    if not stocks or not legs:
        return "", ""

    budget_g = sum(leg.carbs_g for leg in legs)
    packed_g = sum(stock.initial * stock.item.carbs_g for stock in stocks)
    consume_all = packed_g <= budget_g + 0.5
    placed_g = 0.0
    extra_g = 0.0
    last_id: str | None = None
    used_in_leg: list[dict[str, int]] = [{} for _ in legs]
    by_id = {stock.item.id: stock.item for stock in stocks}

    def take(stop: FuelStop) -> bool:
        nonlocal placed_g, last_id
        leg_index = _leg_index_for_km(legs, stop.distance_km)
        blocked: set[str] = set()
        if segments and stop.scheduled_minute < segments[0].start_minute + 0.45 * (
            segments[-1].end_minute - segments[0].start_minute
        ):
            blocked = {stock.item.id for stock in stocks if stock.item.caffeine_mg > 0}
            if blocked and all(stock.remaining <= 0 or stock.item.id in blocked for stock in stocks):
                blocked = set()
        choice = _choose_packed(
            stocks,
            stop.zone,
            last_id,
            used_in_leg[leg_index],
            placed_g,
            budget_g,
            consume_all,
            blocked,
        )
        if choice is None:
            return False
        _stamp_packed(stop, choice.item)
        choice.remaining -= 1
        placed_g += choice.item.carbs_g
        used_in_leg[leg_index][choice.item.id] = used_in_leg[leg_index].get(choice.item.id, 0) + 1
        last_id = choice.item.id
        return True

    for stop in stops:
        if not _is_vest_slot(stop):
            continue
        if all(stock.remaining <= 0 for stock in stocks):
            extra_g += stop.carbs_g
            continue
        if not take(stop) and all(stock.remaining <= 0 for stock in stocks):
            extra_g += stop.carbs_g

    if consume_all and segments and any(stock.remaining > 0 for stock in stocks):
        finish_min = segments[-1].end_minute
        minute = float(feed_interval_min)
        while minute <= finish_min - 10 and any(stock.remaining > 0 for stock in stocks):
            placed = None
            for shift in (12.0, -12.0, 0.0):
                candidate = minute + shift
                if candidate < segments[0].start_minute + PRE_RACE_QUIET_MIN:
                    continue
                if any(abs(stop.scheduled_minute - candidate) < 8 for stop in stops):
                    continue
                placed = _place_feed(segments, candidate, prefer_safe=True)
                if placed is not None:
                    minute = candidate
                    break
            if placed is not None:
                segment, when = placed
                if not any(abs(stop.scheduled_minute - when) < 8 for stop in stops):
                    distance_m = _distance_at_minute(segment, when)
                    location = location_at_distance(course, distance_m)
                    extra_stop = FuelStop(
                        sequence=len(stops) + 1,
                        ideal_minute=minute,
                        scheduled_minute=when,
                        shift_minutes=when - minute,
                        distance_km=distance_m / 1000.0,
                        latitude=location.lat,
                        longitude=location.lon,
                        segment_index=segment.index,
                        grade_percent=segment.grade_percent,
                        zone=segment.zone,
                        carbs_g=1.0,
                        fluid_ml=0.0,
                        sodium_mg=0.0,
                        form=FuelForm.gel,
                        reason="Open vest slot",
                    )
                    if take(extra_stop):
                        stops.append(extra_stop)
            minute += feed_interval_min
        stops.sort(key=lambda stop: (stop.scheduled_minute, stop.sequence))
        for index, stop in enumerate(stops, start=1):
            stop.sequence = index

    for leg in legs:
        kept = [line for line in leg.pack if _keep_pack_line(line)]
        counts: dict[str, int] = {}
        for stop in stops:
            if not stop.reason.startswith("Packed vest:"):
                continue
            if _leg_index_for_km(legs, stop.distance_km) != legs.index(leg):
                continue
            item = next((stock.item for stock in stocks if stock.item.name == stop.product), None)
            if item is None:
                continue
            counts[item.id] = counts.get(item.id, 0) + 1
        for item_id, quantity in counts.items():
            item = by_id[item_id]
            kept.append(
                PackItem(
                    item=item.name,
                    quantity=quantity,
                    detail=f"{item.carbs_g:.0f} g carbs each · from the packed vest",
                )
            )
        leg.pack = kept

    note = ""
    if extra_g >= 1 and all(stock.remaining <= 0 for stock in stocks):
        note = (
            "Packed vest 100% consumed · "
            f"+{int(round(extra_g))}g extra needed from aid stations or extra gels"
        )
    return _inventory_summary(stocks), note


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


def _average_grade(segments: list[Segment], from_m: float, to_m: float) -> float:
    """Net grade of one aid leg, in percent. Negative means a descent."""

    rise = 0.0
    run = 0.0
    for segment in segments:
        overlap_start = max(segment.start_distance_m, from_m)
        overlap_end = min(segment.end_distance_m, to_m)
        if overlap_end <= overlap_start:
            continue
        fraction = (overlap_end - overlap_start) / segment.distance_m
        rise += (segment.elevation_end_m - segment.elevation_start_m) * fraction
        run += overlap_end - overlap_start
    if run < 1:
        return 0.0
    return rise / run * 100.0


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


def _quiet_trail_stop(stop: FuelStop, reason: str) -> None:
    """Turn a trail feed into a water sip. Station plates stay on the table."""

    if stop.at_aid_station or stop.form is FuelForm.rescue_sip:
        return
    stop.carbs_g = 0
    stop.caffeine_mg = 0
    stop.form = FuelForm.sip
    stop.product = "Water"
    stop.flavor = ""
    stop.reason = reason


def _quiet_opening_stops(stops: list[FuelStop], race_start_min: float) -> None:
    """The pre-race meal covers the first 50 minutes. No gel or solid on the trail."""

    limit = race_start_min + PRE_RACE_QUIET_MIN
    for stop in stops:
        if stop.scheduled_minute < limit:
            _quiet_trail_stop(stop, "Pre-race meal covers this window. Water and electrolyte sip only.")


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
) -> None:
    """A fast descent is for pace, not for unwrapping food."""

    for leg in legs:
        grade = _average_grade(segments, leg.from_km * 1000.0, leg.to_km * 1000.0)
        if grade >= DESCENT_LOCK_GRADE:
            continue
        reason = _descent_stop_reason(leg, grade, stations)
        for stop in stops:
            if leg.from_km - 0.05 <= stop.distance_km < leg.to_km - 0.02:
                _quiet_trail_stop(stop, reason)


def build_aid_legs(
    stations: list[AidStation],
    segments: list[Segment],
    athlete: AthleteInput,
    from_distance_m: float,
    total_m: float,
    stops: list[FuelStop] | None = None,
) -> list[AidLeg]:
    """Pack each leg from its duration, not from the stops that happen to fall in it.

    A full race always starts at 0.0 km. The amount to carry is
    leg_hours * hourly_target * 1.15 for carbs, fluid, and sodium.
    """

    marks: list[tuple[str, float]] = []
    if from_distance_m > 1:
        marks.append(("Current position", from_distance_m))
    else:
        marks.append(("Start", 0.0))
    finish_name = "Finish"
    for station in stations:
        distance_m = station.distance_km * 1000.0
        if distance_m >= total_m - FINISH_MERGE_M and distance_m > marks[-1][1] + 30:
            finish_name = station.name
            continue
        if marks[-1][1] + 30 < distance_m < total_m - 30:
            marks.append((station.name, distance_m))
    if total_m > marks[-1][1] + 1:
        marks.append((finish_name, total_m))

    fluid_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    legs: list[AidLeg] = []
    # Carbs eaten at the station we are leaving. They digest into the next leg.
    departure_cover = 0.0
    salt_left = athlete.salt_caps_qty if athlete.has_salt_caps else 0
    for index in range(len(marks) - 1):
        from_name, from_m = marks[index]
        to_name, to_m = marks[index + 1]
        start_min = _minute_at_distance(segments, from_m)
        end_min = _minute_at_distance(segments, to_m)
        minutes = max(0.0, end_min - start_min)
        hours = minutes / 60.0
        carbs_g = hours * athlete.carbs_g_per_hour * PACK_BUFFER
        fluid_ml = hours * fluid_per_hour * PACK_BUFFER
        sodium_mg = hours * sodium_per_hour * PACK_BUFFER
        grade = _average_grade(segments, from_m, to_m)
        leg_stops = [
            stop
            for stop in (stops or [])
            if from_m / 1000.0 - 0.05 <= stop.distance_km < to_m / 1000.0
        ]
        arrival = next((station for station in stations if station.name == to_name), None)
        station_carbs = 0.0
        station_meal = ""
        plate_carbs, plate_label, _plate_sodium = station_plate(athlete)
        arrival_food = arrival is not None and arrival.has_food and minutes >= AID_LEG_MIN_MINUTES
        if arrival_food:
            station_carbs = min(plate_carbs, carbs_g)
            station_meal = plate_label
        rate_buffered = athlete.carbs_g_per_hour * PACK_BUFFER
        if index == 0:
            cover = min(carbs_g, (min(minutes, PRE_RACE_QUIET_MIN) / 60.0) * rate_buffered)
        else:
            window = min(minutes, CARRYOVER_WINDOW_MIN)
            cover = min(departure_cover, carbs_g, (window / 60.0) * rate_buffered)
        trail = max(0.0, carbs_g - station_carbs - cover)
        opening_cap = index == 0 and (
            minutes <= PRE_RACE_SHORT_LEG_MIN
            or (minutes <= PRE_RACE_FOOD_STATION_MIN and arrival_food)
        )
        if grade < DESCENT_LOCK_GRADE:
            cover = max(0.0, carbs_g - station_carbs)
            trail = 0.0
        elif opening_cap:
            trail = min(trail, GEL_CARBS_G)
            cover = max(0.0, carbs_g - station_carbs - trail)
        note = _coach_note(
            opening=index == 0,
            from_name=from_name,
            to_name=to_name,
            minutes=minutes,
            grade=grade,
            arrival_has_food=bool(arrival_food),
            departure_has_food=_departure_has_food(stations, from_name),
        )
        salt_here = 0
        if salt_left > 0 and sodium_mg >= 1 and trail + fluid_ml > 0:
            salt_here = salt_left
            salt_left = 0
        legs.append(
            AidLeg(
                from_name=from_name,
                to_name=to_name,
                from_km=from_m / 1000.0,
                to_km=to_m / 1000.0,
                distance_km=(to_m - from_m) / 1000.0,
                predicted_minutes=minutes,
                carbs_g=carbs_g,
                fluid_ml=fluid_ml,
                sodium_mg=sodium_mg,
                vest_carbs_g=trail,
                station_carbs_g=station_carbs,
                station_meal=station_meal,
                carryover_carbs_g=cover,
                pack=_pack_items(
                    trail,
                    fluid_ml,
                    sodium_mg,
                    athlete.locker,
                    leg_stops,
                    athlete.fuel_preference,
                    _resolved_foods(athlete.vest_foods, VEST_MENU, athlete.custom_vest_items)
                    if athlete.custom_vest_items or not _gels_selected(athlete)
                    else None,
                    hours,
                    start_min,
                    allow_gels=_gels_selected(athlete),
                    salt_caps=salt_here,
                    flask_limit=athlete.flask_count,
                    salt_mg=athlete.salt_cap_mg,
                ),
                note=note,
            )
        )
        departure_cover = station_carbs
    return legs


def snap_custom_aids(course: list[CoursePoint], stations: list[CustomAidInput]) -> list[AidStation]:
    """Move each typed kilometer onto the nearest GPX trackpoint.

    The trackpoint supplies latitude, longitude, elevation, and the kilometer
    the packing list will actually use.
    """

    if not course:
        raise ValueError("A course needs track points before aid stations can be snapped")
    snapped: list[AidStation] = []
    for station in stations:
        target_m = station.distance_km * 1000.0
        point = min(course, key=lambda sample: abs(sample.distance_m - target_m))
        snapped.append(
            AidStation(
                name=station.name.strip(),
                distance_km=point.distance_m / 1000.0,
                lat=point.lat,
                lon=point.lon,
                elevation_m=point.elevation_m,
                has_water=station.has_water,
                has_food=station.has_food,
                cutoff_hours=station.cutoff_hours,
            )
        )
    return snapped


def snap_to_track(points: list[CoursePoint], latitude: float, longitude: float) -> tuple[CoursePoint, float, int]:
    """Project a GPS fix onto the nearest point of the track.

    Each course step is treated as a straight line in a local metre grid
    (equirectangular), then the closest point on that line is kept. This is
    steadier than snapping only to vertices on a switchback.

    Returns the snapped point, the off-course distance in metres, and the
    index of the course step that won.
    """

    if len(points) < 2:
        raise ValueError("Track needs at least two points")

    origin_lat = points[0].lat
    origin_lon = points[0].lon
    px, py = _to_local_xy(latitude, longitude, origin_lat, origin_lon)
    best_off = float("inf")
    best_point = points[0]
    best_index = 0

    for index in range(len(points) - 1):
        left = points[index]
        right = points[index + 1]
        ax, ay = _to_local_xy(left.lat, left.lon, origin_lat, origin_lon)
        bx, by = _to_local_xy(right.lat, right.lon, origin_lat, origin_lon)
        dx = bx - ax
        dy = by - ay
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-9:
            fraction = 0.0
        else:
            fraction = ((px - ax) * dx + (py - ay) * dy) / length_sq
            fraction = min(1.0, max(0.0, fraction))
        sx = ax + fraction * dx
        sy = ay + fraction * dy
        off_m = math.hypot(px - sx, py - sy)
        if off_m < best_off:
            best_off = off_m
            best_index = index
            distance_m = left.distance_m + fraction * (right.distance_m - left.distance_m)
            best_point = location_at_distance(points, distance_m)
    return best_point, best_off, best_index


def recalibrate(
    *,
    race_id: str,
    race_name: str,
    declared_distance_km: float,
    aid_stations: list[AidStation],
    profile: list[TrackPoint],
    athlete: AthleteInput,
    latitude: float,
    longitude: float,
    elapsed_min: float,
    gi_rescue: bool,
    packed_vest_items: list[PackedVestItem] | None = None,
) -> RecalibrateResponse:
    """Snap a live fix to the track and rebuild the plan for the remaining course.

    pace_ratio = elapsed / predicted_time_at_the_snap. A ratio above 1 means
    the runner is behind the effort plan, so every remaining split gets longer.
    GI Rescue, when requested, starts at `elapsed_min`.
    """

    course = prepare_course(profile)
    baseline = build_timed_segments(slice_segments(course), athlete, pace_ratio=1.0)
    snapped, off_course_m, step_index = snap_to_track(course, latitude, longitude)
    predicted_min = _minute_at_distance(baseline, snapped.distance_m)
    pace_ratio = _pace_ratio(elapsed_min, predicted_min)
    segment_index = _segment_index_at(baseline, snapped.distance_m)

    remaining = generate_plan(
        race_id=race_id,
        race_name=race_name,
        declared_distance_km=declared_distance_km,
        aid_stations=aid_stations,
        profile=profile,
        athlete=athlete,
        pace_ratio=pace_ratio,
        not_before_min=elapsed_min,
        from_distance_m=snapped.distance_m,
        rescue_start_min=elapsed_min if gi_rescue else None,
        packed_vest_items=packed_vest_items,
    )
    # The truncated clock is on the scaled timeline, so "now" matches elapsed.
    minutes_behind = elapsed_min - predicted_min
    snap = SnapResult(
        latitude=snapped.lat,
        longitude=snapped.lon,
        distance_along_m=snapped.distance_m,
        distance_along_km=snapped.distance_m / 1000.0,
        off_course_m=off_course_m,
        on_course=off_course_m <= OFF_COURSE_M,
        segment_index=segment_index if segment_index >= 0 else step_index,
        predicted_minute=predicted_min,
        elapsed_minute=elapsed_min,
        pace_ratio=pace_ratio,
    )
    return RecalibrateResponse(
        snap=snap,
        minutes_behind_plan=minutes_behind,
        gi_rescue_applied=gi_rescue,
        message=_recalibrate_message(snap, minutes_behind, gi_rescue),
        remaining=remaining,
    )


def load_race_catalog(path: Path | None = None) -> list[Race]:
    """Read `backend/data/races.json`."""

    catalog_path = path or Path(__file__).resolve().parent / "data" / "races.json"
    payload = json.loads(catalog_path.read_text(encoding="utf-8"))
    return [Race.model_validate(item) for item in payload["races"]]


def get_race(race_id: str, path: Path | None = None) -> Race:
    """Return one stored race, or raise KeyError."""

    for race in load_race_catalog(path):
        if race.id == race_id:
            return race
    raise KeyError(race_id)


def _pace_ratio(elapsed_min: float, predicted_min: float) -> float:
    if predicted_min < MIN_PACE_SAMPLE_MIN:
        return 1.0
    raw = elapsed_min / predicted_min
    return min(MAX_PACE_RATIO, max(MIN_PACE_RATIO, raw))


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


def _place_feed(
    segments: list[Segment],
    ideal_min: float,
    prefer_safe: bool,
) -> tuple[Segment, float] | None:
    """Pick a segment overlapping [ideal-6, ideal+6] and the time to use on it.

    Safe feeds prefer green, then yellow, then red, and only then the smallest
    clock shift. A water sip ignores zone and stays as close to the ideal time
    as it can.
    """

    window_lo = ideal_min - WINDOW_MIN
    window_hi = ideal_min + WINDOW_MIN
    candidates: list[tuple[tuple[float, float, int], Segment, float]] = []
    for segment in segments:
        overlap_lo = max(segment.start_minute, window_lo)
        overlap_hi = min(segment.end_minute, window_hi)
        if overlap_lo > overlap_hi:
            continue
        when = min(max(ideal_min, overlap_lo), overlap_hi)
        zone_rank = _zone_rank(segment.zone) if prefer_safe else 0.0
        rank = (zone_rank, abs(when - ideal_min), float(segment.index))
        candidates.append((rank, segment, when))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, segment, when = candidates[0]
    return segment, when


def _dose_for_target(
    *,
    target: _Target,
    zone: DigestiveZone,
    grade_percent: float,
    shift_min: float,
    athlete: AthleteInput,
    gentle_left: int,
) -> _Dose:
    """Decide carbs, fluid, sodium, and the sentence the runner should read.

    A window never receives carbs skipped by an earlier window.
    """

    hours = athlete.feed_interval_min / 60.0
    base_carbs = athlete.carbs_g_per_hour * hours
    ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    base_fluid = ml_per_hour * hours
    base_sodium = sodium_per_hour * hours
    grade_text = f"{grade_percent:+.1f}%"

    if target.kind == "rescue_nil":
        return _Dose(
            0.0,
            RESCUE_SIP_FLUID_ML,
            RESCUE_SIP_SODIUM_MG,
            FuelForm.rescue_sip,
            gentle_left,
            "GI Rescue: no calories yet. Take a small water and salt sip only.",
        )
    if target.kind == "rescue_ease":
        if zone == DigestiveZone.red:
            return _Dose(
                0.0,
                RESCUE_SIP_FLUID_ML,
                RESCUE_SIP_SODIUM_MG,
                FuelForm.rescue_sip,
                gentle_left,
                f"GI Rescue: grade {grade_text} is still red, so skip calories and sip.",
            )
        return _Dose(
            RESCUE_EASE_CARBS_G,
            RESCUE_EASE_FLUID_ML,
            RESCUE_EASE_SODIUM_MG,
            FuelForm.liquid,
            gentle_left,
            f"GI Rescue: {RESCUE_EASE_CARBS_G:.0f} g of liquid carb while the stomach settles.",
        )

    if zone == DigestiveZone.green:
        carbs = base_carbs
        form = FuelForm.gel
        reason = _green_reason(shift_min, grade_text, carbs)
    elif zone == DigestiveZone.yellow:
        carbs = base_carbs * 0.5
        form = FuelForm.liquid
        reason = _yellow_reason(shift_min, grade_text, carbs)
    else:
        carbs = min(RED_MICRO_DOSE_G, base_carbs)
        forfeited = base_carbs - carbs
        form = FuelForm.micro_dose
        reason = (
            f"No green or yellow ground inside +/-{WINDOW_MIN:.0f} min ({grade_text}). "
            f"Liquid carb micro-dose: {carbs:.0f} g from drink mix, in 2-3 small sips over 5 minutes."
        )
        if forfeited >= 1:
            reason += f" The other {forfeited:.0f} g in this window is forfeited."

    if gentle_left > 0 and carbs > 0:
        carbs *= 0.5
        gentle_left -= 1
        reason += " Half-size while GI Rescue eases off."

    return _Dose(carbs, base_fluid, base_sodium, form, gentle_left, reason)


def _align_reason_with_intake(reason: str, form: FuelForm, product: str, carbs: float) -> str:
    """Name the food this stop actually uses.

    The dose sentence is written before the vest menu picks a gel, a drink,
    or real food. This only rewrites that sentence.
    """

    name = product.strip()
    if form is FuelForm.gel:
        spoken = f"take a {carbs:.0f} g {name or 'gel'}"
        half = f"Half dose as gel ({carbs:.0f} g{', ' + name if name else ''})"
    elif form is FuelForm.liquid:
        spoken = f"take {carbs:.0f} g of {name or 'liquid'}"
        half = f"Half dose as liquid ({carbs:.0f} g{', ' + name if name else ''})"
    elif form is FuelForm.real_food:
        spoken = f"take {carbs:.0f} g of {name or 'real food'}"
        half = f"Half dose as real food ({carbs:.0f} g{', ' + name if name else ''})"
    else:
        return reason
    reason = _replace_gel_claim(reason, spoken)
    return _replace_liquid_half_dose(reason, half)


def _replace_gel_claim(reason: str, spoken: str) -> str:
    """Swap 'take a 30 g gel' for the product that was chosen."""

    for needle in ("take a ", "Take a "):
        start = reason.find(needle)
        if start == -1:
            continue
        end = reason.find(" g gel", start)
        if end == -1:
            continue
        grams = reason[start + len(needle):end]
        if not grams.isdigit():
            continue
        phrase = spoken[0].upper() + spoken[1:] if needle.startswith("T") else spoken
        return reason[:start] + phrase + reason[end + len(" g gel"):]
    return reason


def _replace_liquid_half_dose(reason: str, half: str) -> str:
    prefix = "Half dose as liquid ("
    start = reason.find(prefix)
    if start == -1:
        return reason
    end = reason.find(")", start)
    if end == -1:
        return reason
    return reason[:start] + half + reason[end + 1:]


def _green_reason(shift_min: float, grade_text: str, carbs: float) -> str:
    if abs(shift_min) < 0.25:
        return f"On time. Grade {grade_text} is green, so take a {carbs:.0f} g gel."
    return (
        f"Moved {shift_min:+.1f} min onto a green segment ({grade_text}). "
        f"Take a {carbs:.0f} g gel."
    )


def _yellow_reason(shift_min: float, grade_text: str, carbs: float) -> str:
    if abs(shift_min) < 0.25:
        text = f"On time, but grade {grade_text} is yellow."
    else:
        text = f"Moved {shift_min:+.1f} min onto a yellow segment ({grade_text})."
    return text + f" Half dose as liquid ({carbs:.0f} g). Nothing from this window is carried forward."


def _truncate_front(
    segments: list[Segment],
    course: list[CoursePoint],
    distance_m: float,
) -> list[Segment]:
    """Drop finished slices and shorten the slice the runner is currently in.

    The shortened slice keeps its parent's grade and zone so the race clock
    stays continuous. Rebuilding the grade from only the leftover metres would
    shift every later split.
    """

    kept: list[Segment] = []
    for segment in segments:
        if segment.end_distance_m <= distance_m + 1:
            continue
        if segment.start_distance_m >= distance_m - 1:
            kept.append(segment)
            continue
        fraction_done = (distance_m - segment.start_distance_m) / segment.distance_m
        fraction_done = min(max(fraction_done, 0.0), 1.0)
        location = location_at_distance(course, distance_m)
        remaining_m = segment.end_distance_m - distance_m
        kept.append(
            segment.model_copy(
                update={
                    "start_distance_m": distance_m,
                    "distance_m": remaining_m,
                    "start_lat": location.lat,
                    "start_lon": location.lon,
                    "elevation_start_m": location.elevation_m,
                    "elevation_gain_m": segment.elevation_gain_m * (1 - fraction_done),
                    "elevation_loss_m": segment.elevation_loss_m * (1 - fraction_done),
                    "predicted_minutes": segment.predicted_minutes * (1 - fraction_done),
                    "start_minute": segment.start_minute + fraction_done * segment.predicted_minutes,
                    "predicted_pace_min_per_km": segment.predicted_pace_min_per_km,
                }
            )
        )
    return kept


def _minute_at_distance(segments: list[Segment], distance_m: float) -> float:
    if not segments:
        return 0.0
    if distance_m <= segments[0].start_distance_m:
        return segments[0].start_minute
    for segment in segments:
        if segment.start_distance_m <= distance_m <= segment.end_distance_m:
            span = segment.end_distance_m - segment.start_distance_m
            fraction = 0.0 if span <= 0 else (distance_m - segment.start_distance_m) / span
            return segment.start_minute + fraction * segment.predicted_minutes
    return segments[-1].end_minute


def _distance_at_minute(segment: Segment, minute: float) -> float:
    span = segment.predicted_minutes
    fraction = 0.0 if span <= 0 else (minute - segment.start_minute) / span
    fraction = min(max(fraction, 0.0), 1.0)
    return segment.start_distance_m + fraction * segment.distance_m


def _segment_index_at(segments: list[Segment], distance_m: float) -> int:
    for segment in segments:
        if segment.start_distance_m <= distance_m <= segment.end_distance_m:
            return segment.index
    if segments and distance_m > segments[-1].end_distance_m:
        return segments[-1].index
    return 0


def _savoury_minute(athlete: AthleteInput, finish_min: float) -> float:
    """When green feeds should leave the sweet rotation.

    The gut quiz can start that at 1.5 h. Otherwise it starts at 2.5 h, and a
    race longer than 3.5 h still switches by 3.5 h or halfway, whichever the
    clock reaches first among those marks.
    """

    start = EARLY_SAVOURY_MIN if athlete.early_savoury else SAVOURY_START_MIN
    if finish_min > PALATE_FATIGUE_MIN:
        start = min(start, max(PALATE_FATIGUE_MIN, finish_min / 2.0))
    return start


def _pick_item(items: list[_MenuItem], when: float) -> _MenuItem:
    return items[int(when // 30) % len(items)]


def _apply_named_food(
    dose: _Dose,
    item: _MenuItem,
    note: str,
    *,
    portion_scale: float = 1.0,
) -> tuple[str, str]:
    """Attach a menu item.

    Catalog foods keep the carb grams the window already chose (usually 30 g).
    A portion the runner edited replaces those grams, so the live prompt says
    "Milka Chocolate (22g)" instead of the generic window.
    """

    if item.uses_portion:
        dose.carbs_g = max(1.0, float(round(item.carbs_g * portion_scale)))
    if item.kind == "solid":
        dose.form = FuelForm.real_food
    elif item.kind == "liquid":
        dose.form = FuelForm.liquid
    else:
        dose.form = FuelForm.gel
    dose.reason += f" Flavor: {item.flavor}. {note}{item.name}."
    return item.name, item.flavor


def _apply_food_preference(
    dose: _Dose,
    *,
    grade_percent: float,
    zone: DigestiveZone,
    when: float,
    finish_min: float,
    athlete: AthleteInput,
    clock: _MenuClock | None = None,
) -> tuple[str, str]:
    """Name a vest food from the runner's menu, and mark the flavor.

    Red micro-doses and rescue sips stay liquid. The first 90 minutes stay on
    gels, drink mix, or one small bar or dates. After that, a solid is at most
    one bite per 90 minutes. Yellow stays on gels and drink mix.
    """

    if dose.carbs_g <= 0 or dose.form in {FuelForm.rescue_sip, FuelForm.sip}:
        return "", ""
    # A red micro-dose stays a plain liquid sip when a gel is in the vest.
    # With no gel ticked, the same 15 g has to come from drink mix or tiny bites.
    if dose.form is FuelForm.micro_dose and _gels_selected(athlete):
        return "", ""
    if not _gels_selected(athlete) and (dose.form is FuelForm.micro_dose or zone in {DigestiveZone.red, DigestiveZone.yellow}):
        return _climb_without_gel(dose, athlete, zone)

    foods = _resolved_foods(athlete.vest_foods, VEST_MENU, athlete.custom_vest_items)
    if not _gels_selected(athlete):
        foods = [item for item in foods if item.kind != "gel" and item.key not in GEL_VEST_KEYS]
    elif not foods:
        foods = [VEST_MENU["gel_25"]]
    if not foods:
        dose.form = FuelForm.sip
        dose.carbs_g = 0
        dose.reason += " No gel is in the vest, and no other vest food is selected."
        return "", ""
    if clock is None:
        clock = _MenuClock()
    easy = [item for item in foods if item.kind in {"gel", "liquid"}]
    small = [item for item in foods if item.key in {"dates", "bar"}]
    light = [item for item in foods if item.key not in HEAVY_VEST_KEYS]

    def pick(pool: list[_MenuItem], note: str = "Take ", scale: float = 1.0) -> tuple[str, str]:
        return _apply_named_food(dose, _pick_item(pool, when), note, portion_scale=scale)

    if zone == DigestiveZone.yellow:
        return pick(easy or light or foods[:1], "Half portion: ", 0.5)
    if zone != DigestiveZone.green:
        return "", ""
    solid_blocked = (
        grade_percent > SOLID_CLEAR_CLIMB
        or grade_percent < SOLID_CLEAR_DESCENT
        or (finish_min - when < 45 and finish_min > 360)
    )
    if when < OPENING_EASY_MIN:
        if not clock.opening_solid and small and when >= PRE_RACE_QUIET_MIN and not solid_blocked:
            clock.opening_solid = True
            clock.last_solid_min = when
            return pick(small[:1])
        return pick(easy or light or foods[:1])

    due = when - clock.last_solid_min >= SOLID_EVERY_MIN
    if due and not solid_blocked:
        if when >= _savoury_minute(athlete, finish_min):
            savoury = [item for item in foods if item.flavor == "Savoury/Salty" or item.key in SAVOURY_VEST_KEYS]
            solids = [item for item in savoury if item.kind == "solid"] or [
                item for item in foods if item.kind == "solid"
            ]
            if solids:
                clock.last_solid_min = when
                return pick(solids)
        else:
            between = [item for item in foods if item.kind == "solid" and item.key != "sandwich"]
            if between:
                clock.last_solid_min = when
                return pick(between)
    return pick(easy or foods[:1])


def _climb_without_gel(dose: _Dose, athlete: AthleteInput, zone: DigestiveZone) -> tuple[str, str]:
    """Cover a hard climb without inventing a gel.

    Drink mix is the fast substitute. If that is off too, the softest solid
    is eaten in tiny bites and the carb count stays the small climb dose.
    """

    foods = [
        item
        for item in _resolved_foods(athlete.vest_foods, VEST_MENU, athlete.custom_vest_items)
        if item.kind != "gel" and item.key not in GEL_VEST_KEYS
    ]
    drink = next((item for item in foods if item.key == "drink_30" or item.kind == "liquid"), None)
    if drink is not None:
        dose.form = FuelForm.micro_dose if zone is DigestiveZone.red else FuelForm.liquid
        dose.reason += f" No gel in the vest. {drink.name} covers this climb."
        return drink.name, drink.flavor
    soft: _MenuItem | None = None
    for key in ("dates", "chocolate", "bar", "chips", "sandwich"):
        soft = next((item for item in foods if item.key == key), None)
        if soft is not None:
            break
    if soft is None:
        soft = next((item for item in foods if item.kind == "solid"), None)
    if soft is None:
        dose.form = FuelForm.sip
        dose.carbs_g = 0
        dose.reason += " No gel or drink mix in the vest. Sip water and salt on this climb."
        return "", ""
    dose.form = FuelForm.micro_dose if zone is DigestiveZone.red else FuelForm.real_food
    dose.reason += f" No gel or drink mix. Take tiny bites of {soft.name}."
    return soft.name, soft.flavor


def _approach_minutes(
    segments: list[Segment],
    stations: list[AidStation],
    distance_m: float,
) -> tuple[float, float]:
    """Minutes spent on the leg that arrives at `distance_m`, and the arrival clock."""

    previous_m = segments[0].start_distance_m if segments else 0.0
    for station in sorted(stations, key=lambda item: item.distance_km):
        station_m = station.distance_km * 1000.0
        if station_m >= distance_m - 1:
            break
        if station_m > previous_m:
            previous_m = station_m
    start = _minute_at_distance(segments, previous_m)
    end = _minute_at_distance(segments, distance_m)
    return max(0.0, end - start), end


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


def _wants_caffeine(segments: list[Segment], when: float, race_start: float, finish_min: float) -> bool:
    """True before a steep climb, and for every feed in the second half.

    The race clock runs from the start, so halfway is half of the finish
    minute. A plan that only covers the road ahead still uses that clock:
    once the runner is already past halfway, later gels may carry caffeine.
    """

    # Caffeine stays in the vest until 45% of the race, then it is aimed
    # about half an hour before a climb so the peak lands on the slope.
    span = max(1.0, finish_min - race_start)
    if when < race_start + 0.45 * span and race_start < finish_min * 0.45:
        return False
    for segment in segments:
        if segment.end_minute < when:
            continue
        lead = segment.start_minute - when
        if lead > 35:
            break
        if 30 <= lead <= 35 and segment.grade_percent >= MAJOR_CLIMB_GRADE:
            return True
    return True


def _apply_locker(
    dose: _Dose,
    segments: list[Segment],
    when: float,
    race_start: float,
    finish_min: float,
    athlete: AthleteInput,
    caffeine_used: float,
) -> tuple[str, float, str]:
    """Name the locker product and add caffeine only when the cap allows it.

    An empty locker leaves the dose sentence unchanged, so a plan with no
    custom products still reads as a plain gel. Caffeine is never attached
    to a yellow sip, a red micro-dose, or a GI-rescue sip.
    """

    locker = athlete.locker
    if not locker or dose.form != FuelForm.gel or dose.carbs_g <= 0:
        return "", 0.0, dose.reason

    caffeinated = next((item for item in locker if item.kind == "gel" and item.caffeine_mg > 0), None)
    plain = next((item for item in locker if item.caffeine_mg <= 0), None)
    cap = athlete.weight_kg * CAFFEINE_MG_PER_KG
    wants = _wants_caffeine(segments, when, race_start, finish_min)
    if wants and caffeinated is not None and caffeine_used + caffeinated.caffeine_mg <= cap + 1e-6:
        used = caffeinated.caffeine_mg
        reason = (
            f"{dose.reason} Use {caffeinated.name} "
            f"({used:.0f} mg caffeine). "
            f"Total after this stop: {caffeine_used + used:.0f} / {cap:.0f} mg."
        )
        return caffeinated.name, used, reason

    blocked = ""
    if wants and caffeinated is not None:
        blocked = f" Caffeine cap is {cap:.0f} mg, so this stop stays uncaffeinated."
    if plain is not None:
        return plain.name, 0.0, f"{dose.reason} Use {plain.name}.{blocked}"
    return "", 0.0, dose.reason + blocked


def _flask_item(
    fluid_ml: float,
    leg_hours: float,
    mix_note: str = "",
    flask_limit: int | None = None,
) -> PackItem | None:
    """Two 500 ml flasks, or three on a leg longer than 2.5 hours.

    A vest cannot carry four litres. Extra fluid is a mid-leg refill, not
    another bottle in the pack list.
    """

    if fluid_ml < 1:
        return None
    cap = FLASK_CAP_LONG if leg_hours > LONG_LEG_HOURS else FLASK_CAP
    if flask_limit is not None:
        cap = min(cap, flask_limit)
    if cap <= 0:
        return None
    needed = math.ceil(fluid_ml / FLASK_ML)
    carry = min(needed, cap)
    if needed > carry:
        refills = needed - carry
        note = f"Refill {refills}× at mid-leg fountain/stream — total {round(fluid_ml):,} ml."
    else:
        note = "Water/Electrolytes."
    detail = f"{mix_note} {note}".strip() if mix_note else note
    return PackItem(item="500ml Flask", quantity=carry, detail=detail)


def _eat_detail(portion_g: float, count: int, leg_minutes: float, earliest_min: float = 0.0) -> str:
    """Space bites across the leg. Two gels on a 105 minute leg land at 35 and 70.

    `earliest_min` keeps a sandwich out of the first 90 minutes when a leg
    starts at the gun and only runs past that mark near the end.
    """

    if count <= 0 or leg_minutes <= 0:
        return f"{portion_g:.0f}g each."
    span_start = min(max(leg_minutes - 1, 0.0), max(0.0, earliest_min))
    span = max(1.0, leg_minutes - span_start)
    gap = span / (count + 1)
    clocks = [max(1, int(round(span_start + gap * (index + 1)))) for index in range(count)]
    if len(clocks) > 4:
        shown = ", ".join(f"min {minute}" for minute in clocks[:4]) + f", +{len(clocks) - 4} more"
    else:
        shown = ", ".join(f"min {minute}" for minute in clocks)
    return f"{portion_g:.0f}g each. Eat at {shown} of this leg."


def _solid_choice(
    foods: list[_MenuItem],
    by_key: dict[str, _MenuItem],
    leg_start_min: float,
    leg_minutes: float,
) -> tuple[_MenuItem | None, int, float]:
    """Pick the one solid for this leg, how many, and the earliest eat minute.

    A leg that is still inside the first 90 minutes gets one bar or dates.
    A sandwich is only carried once the leg has 45 minutes after that opening,
    and its eat time starts after minute 90.
    """

    leg_end = leg_start_min + leg_minutes
    heavy_time = max(0.0, leg_end - max(leg_start_min, OPENING_EASY_MIN))
    if heavy_time >= 45:
        slots = max(1, int(heavy_time // SOLID_EVERY_MIN))
        solid = None
        for key in ("sandwich", "bar", "chocolate", "chips", "dates"):
            if key in by_key:
                solid = by_key[key]
                break
        if solid is None:
            solid = next((food for food in foods if food.kind == "solid"), None)
        return solid, slots if solid is not None else 0, max(0.0, OPENING_EASY_MIN - leg_start_min)
    if leg_start_min < OPENING_EASY_MIN:
        for key in ("dates", "bar"):
            if key in by_key:
                return by_key[key], 1, 0.0
        return None, 0, 0.0
    if leg_minutes < 45:
        return None, 0, 0.0
    slots = max(1, int(leg_minutes // SOLID_EVERY_MIN))
    for key in ("sandwich", "bar", "chocolate", "chips", "dates"):
        if key in by_key:
            return by_key[key], slots, 0.0
    solid = next((food for food in foods if food.kind == "solid"), None)
    return solid, slots if solid is not None else 0, 0.0


def _pack_named_portions(
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    locker: list[LockerItem],
    foods: list[_MenuItem],
    stops: list[FuelStop] | None,
    leg_hours: float,
    leg_start_min: float,
    allow_gels: bool = True,
    salt_caps: int = 0,
    flask_limit: int | None = None,
    salt_mg: float = CAPSULE_SODIUM_MG,
) -> list[PackItem]:
    """Pack at most two foods to chew, with drink mix poured into one flask.

    A running vest is not a grocery bag. One gel type plus one solid, and
    solids stay at one bite per 90 minutes. The first 90 minutes of the race
    get no sandwich.
    """

    if not allow_gels:
        foods = [food for food in foods if food.kind != "gel" and food.key not in GEL_VEST_KEYS]
    items: list[PackItem] = []
    covered = 0.0
    for product in locker:
        if not allow_gels or product.kind != "gel" or product.caffeine_mg <= 0:
            continue
        count = sum(1 for stop in (stops or []) if stop.product == product.name and stop.caffeine_mg > 0)
        if count <= 0:
            continue
        items.append(
            PackItem(
                item=product.name,
                quantity=count,
                detail=f"{product.carbs_g:.0f} g carbs and {product.caffeine_mg:.0f} mg caffeine each.",
            )
        )
        covered += count * product.carbs_g

    remaining = max(0.0, carbs_g - covered)
    leg_minutes = max(0.0, leg_hours * 60.0)
    by_key = {food.key: food for food in foods if food.key}
    drink = by_key.get("drink_30") or next((food for food in foods if food.kind == "liquid"), None)
    mix_note = ""
    if drink is not None and remaining >= drink.carbs_g:
        remaining -= drink.carbs_g
        mix_note = f"1× {drink.name} ({drink.carbs_g:.0f}g) in one flask."

    gel = None
    if allow_gels and ("gel_25" in by_key or "iso_gel_22" in by_key):
        gel = by_key.get("gel_25") or by_key.get("iso_gel_22")
    solid, slots, earliest_min = _solid_choice(foods, by_key, leg_start_min, leg_minutes)

    food_lines: list[PackItem] = []
    if solid is not None and slots > 0 and remaining >= solid.carbs_g:
        quantity = min(slots, int(remaining // solid.carbs_g))
        if quantity >= 1:
            food_lines.append(
                PackItem(
                    item=solid.name,
                    quantity=quantity,
                    detail=_eat_detail(solid.carbs_g, quantity, leg_minutes, earliest_min),
                )
            )
            remaining -= quantity * solid.carbs_g
    if gel is not None and remaining >= 1:
        quantity = math.ceil(remaining / gel.carbs_g)
        food_lines.append(
            PackItem(
                item=gel.name,
                quantity=quantity,
                detail=_eat_detail(gel.carbs_g, quantity, leg_minutes),
            )
        )
        remaining = 0
    elif remaining >= 1 and foods and not food_lines:
        only = foods[0]
        food_lines.append(
            PackItem(
                item=only.name,
                quantity=1,
                detail=_eat_detail(only.carbs_g, 1, leg_minutes),
            )
        )

    flask = _flask_item(fluid_ml, leg_hours, mix_note, flask_limit=flask_limit)
    packed: list[PackItem] = []
    if flask is not None:
        packed.append(flask)
    packed.extend(items)
    gels = [line for line in food_lines if solid is None or line.item != solid.name]
    solids = [line for line in food_lines if solid is not None and line.item == solid.name]
    packed.extend(gels)
    packed.extend(solids)
    if salt_caps > 0:
        packed.append(PackItem(item="Salt capsule", quantity=salt_caps, detail=f"{salt_mg:.0f} mg sodium each."))
    return packed


def _pack_items(
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    locker: list[LockerItem] | None = None,
    stops: list[FuelStop] | None = None,
    preference: str = "gels_first",
    named_foods: list[_MenuItem] | None = None,
    leg_hours: float = 0.0,
    leg_start_min: float = 0.0,
    allow_gels: bool = True,
    salt_caps: int = 0,
    flask_limit: int | None = None,
    salt_mg: float = CAPSULE_SODIUM_MG,
) -> list[PackItem]:
    """Turn an already-buffered vest total into food, flasks, and salt.

    `carbs_g` is what goes in the vest after any aid-station plate is subtracted.
    An empty locker follows `preference`. A locker still packs the runner's own items.
    Named portions are used only when the runner sent custom vest foods.
    """

    if named_foods is not None:
        return _pack_named_portions(
            carbs_g,
            fluid_ml,
            sodium_mg,
            locker or [],
            named_foods,
            stops,
            leg_hours,
            leg_start_min,
            allow_gels,
            salt_caps=salt_caps,
            flask_limit=flask_limit,
            salt_mg=salt_mg,
        )
    if not locker:
        return _pack_for_preference(
            carbs_g,
            fluid_ml,
            sodium_mg,
            preference,
            leg_hours,
            salt_caps=salt_caps,
            flask_limit=flask_limit,
            salt_mg=salt_mg,
        )

    items: list[PackItem] = []
    covered = 0.0
    for product in locker:
        if product.kind != "gel" or product.caffeine_mg <= 0:
            continue
        count = sum(1 for stop in (stops or []) if stop.product == product.name and stop.caffeine_mg > 0)
        if count <= 0:
            continue
        items.append(
            PackItem(
                item=product.name,
                quantity=count,
                detail=f"{product.carbs_g:.0f} g carbs and {product.caffeine_mg:.0f} mg caffeine each.",
            )
        )
        covered += count * product.carbs_g

    plain = next((item for item in locker if item.caffeine_mg <= 0 and item.kind == "gel"), None)
    if plain is None:
        plain = next((item for item in locker if item.caffeine_mg <= 0), None)
    remaining = max(0.0, carbs_g - covered)
    if plain is not None and remaining >= 1:
        quantity = math.ceil(remaining / plain.carbs_g)
        items.append(
            PackItem(
                item=plain.name,
                quantity=quantity,
                detail=f"{plain.carbs_g:.0f} g carbs each. The leg total already includes the 15% reserve.",
            )
        )
    elif remaining >= 1:
        items.extend(_pack_plain(remaining, 0, 0))

    flask = _flask_item(fluid_ml, leg_hours, flask_limit=flask_limit)
    if flask is not None:
        liquid = next((item for item in locker if item.kind == "liquid"), None)
        if liquid is None:
            items.append(flask)
        else:
            items.append(
                PackItem(
                    item=liquid.name,
                    quantity=flask.quantity,
                    detail=f"{liquid.carbs_g:.0f} g carbs per 500 ml. {flask.detail}",
                )
            )
    if salt_caps > 0:
        items.append(
            PackItem(
                item="Salt capsule",
                quantity=salt_caps,
                detail=f"{salt_mg:.0f} mg sodium each.",
            )
        )
    return items


def _pack_for_preference(
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    preference: str,
    leg_hours: float = 0.0,
    salt_caps: int = 0,
    flask_limit: int | None = None,
    salt_mg: float = CAPSULE_SODIUM_MG,
) -> list[PackItem]:
    """Split vest carbs into gels, bars, and savoury food, then add flasks and salt.

    A short vest under 60 g stays gels, so the small packing check still sees
    two 25 g gels and no bar.
    """

    items: list[PackItem] = []
    remaining = carbs_g
    if preference == "real_food_heavy":
        if remaining >= 22:
            items.append(PackItem(item="Isotonic gel", quantity=1, detail="22 g backup for yellow or red ground."))
            remaining -= 22
        rice = math.ceil((remaining * 0.5) / 20.0) if remaining >= 1 else 0
        rice_carbs = min(remaining, rice * 20.0)
        if rice:
            items.append(PackItem(item="Salted rice cake", quantity=rice, detail="20 g carbs each."))
            remaining -= rice_carbs
        if remaining >= 1:
            bars = math.ceil(remaining / 35.0)
            items.append(PackItem(item="Energy bar", quantity=bars, detail="35 g carbs each."))
    elif preference == "balanced_real_food":
        gel_carbs = remaining * 0.4
        bar_carbs = remaining * 0.3
        rice_carbs = remaining - gel_carbs - bar_carbs
        gels = math.ceil(gel_carbs / GEL_CARBS_G) if gel_carbs >= 1 else 0
        bars = math.ceil(bar_carbs / 35.0) if bar_carbs >= 1 else 0
        rice = math.ceil(rice_carbs / 20.0) if rice_carbs >= 1 else 0
        if gels:
            items.append(PackItem(item="Gel", quantity=gels, detail="25 g carbs each."))
        if bars:
            items.append(PackItem(item="Energy bar", quantity=bars, detail="35 g carbs each."))
        if rice:
            items.append(PackItem(item="Salted rice cake", quantity=rice, detail="20 g carbs each."))
    else:
        if remaining >= 60:
            items.append(PackItem(item="Energy bar", quantity=1, detail="35 g carbs. Flat-green spare so the vest is not only gels."))
            remaining -= 35
        if remaining >= 1:
            gels = math.ceil(remaining / GEL_CARBS_G)
            items.append(PackItem(item="Gel", quantity=gels, detail="25 g carbs each. The leg total already includes the 15% reserve."))
    items.extend(_pack_plain(0, fluid_ml, 0, leg_hours, salt_caps=salt_caps, flask_limit=flask_limit, salt_mg=salt_mg))
    return items


def _pack_plain(
    carbs_g: float,
    fluid_ml: float,
    sodium_mg: float,
    leg_hours: float = 0.0,
    salt_caps: int = 0,
    flask_limit: int | None = None,
    salt_mg: float = CAPSULE_SODIUM_MG,
) -> list[PackItem]:
    """Default pack: 25 g gels, the flasks the runner carries, and salt only if asked."""

    del sodium_mg
    items: list[PackItem] = []
    if carbs_g >= 1:
        gels = math.ceil(carbs_g / GEL_CARBS_G)
        items.append(PackItem(item="Gel", quantity=gels, detail="25 g carbs each. The leg total already includes the 15% reserve."))
    flask = _flask_item(fluid_ml, leg_hours, flask_limit=flask_limit)
    if flask is not None:
        items.append(flask)
    if salt_caps > 0:
        items.append(PackItem(item="Salt capsule", quantity=salt_caps, detail=f"{salt_mg:.0f} mg sodium each."))
    return items


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


def _zone_rank(zone: DigestiveZone) -> float:
    return {DigestiveZone.green: 0.0, DigestiveZone.yellow: 1.0, DigestiveZone.red: 2.0}[zone]


def _to_local_xy(lat: float, lon: float, origin_lat: float, origin_lon: float) -> tuple[float, float]:
    metres_per_deg_lon = METERS_PER_DEG_LAT * math.cos(math.radians(origin_lat))
    x = (lon - origin_lon) * metres_per_deg_lon
    y = (lat - origin_lat) * METERS_PER_DEG_LAT
    return x, y


def _recalibrate_message(snap: SnapResult, minutes_behind: float, gi_rescue: bool) -> str:
    if snap.predicted_minute < MIN_PACE_SAMPLE_MIN:
        pace_text = "Still near the start, so the original effort plan is unchanged."
    elif minutes_behind > 1:
        pace_text = (
            f"You are {minutes_behind:.0f} min behind the effort plan. "
            "Remaining splits were stretched to match your current pace."
        )
    elif minutes_behind < -1:
        pace_text = (
            f"You are {-minutes_behind:.0f} min ahead of the effort plan. "
            "Remaining splits were compressed."
        )
    else:
        pace_text = "You are on the planned effort. Terrain windows are unchanged."

    if not snap.on_course:
        pace_text += f" GPS is {snap.off_course_m:.0f} m off the track; the nearest point on course was used."
    if gi_rescue:
        pace_text += " GI Rescue is on: solids are paused, and the next reminders are small fluid-and-salt sips."
    return pace_text


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


if __name__ == "__main__":
    run_self_check()
