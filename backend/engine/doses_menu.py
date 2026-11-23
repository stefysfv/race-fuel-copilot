"""Food menus, dose sizes, and the words on each feed."""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.models import (
    AthleteInput,
    CustomFoodItem,
    DigestiveZone,
    FuelForm,
    ReasonCode,
    Segment,
)

from backend.engine.reasons import make_reason

from backend.engine.constants import (
    AID_BITE_CARBS_G,
    AID_PLATE,
    CAFFEINE_MG_PER_KG,
    DEFAULT_STATION_KEYS,
    EARLY_SAVOURY_MIN,
    FRUIT_STATION_KEYS,
    GEL_VEST_KEYS,
    HEAVY_VEST_KEYS,
    MAJOR_CLIMB_GRADE,
    OPENING_EASY_MIN,
    PALATE_FATIGUE_MIN,
    PRE_RACE_QUIET_MIN,
    RED_MICRO_DOSE_G,
    RESCUE_EASE_CARBS_G,
    RESCUE_EASE_FLUID_ML,
    RESCUE_EASE_SODIUM_MG,
    RESCUE_SIP_FLUID_ML,
    RESCUE_SIP_SODIUM_MG,
    SALT_STATION_KEYS,
    SAVOURY_START_MIN,
    SAVOURY_VEST_KEYS,
    SOLID_CLEAR_CLIMB,
    SOLID_CLEAR_DESCENT,
    SOLID_EVERY_MIN,
    WINDOW_MIN,
)

from backend.engine.sweat import sweat_rates


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
    "banana": _MenuItem("banana", "Banana", 22, 1, "Sweet", "solid"),
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
    offset_min: float = 0.0  # minutes after a rescue start; 0 for a normal slot


def _remember_drop(drops: list, carbs_g: float, code: ReasonCode, **params: float) -> None:
    """Keep a positive carb cut so the scheduler can place it on the clock."""

    if carbs_g <= 0:
        return
    drops.append((float(carbs_g), code, {key: float(value) for key, value in params.items()}))


@dataclass
class _Dose:
    carbs_g: float
    fluid_ml: float
    sodium_mg: float
    form: FuelForm
    gentle_left: int
    reason: str
    reasons: list = field(default_factory=list)
    # (carbs dropped, code, params). The scheduler stamps the clock minute.
    drops: list = field(default_factory=list)


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
    reasons: list = []
    drops: list = []

    if target.kind == "rescue_nil":
        # The sip is defined as 0 g, so it is not a forfeited row.
        reasons.append(make_reason(ReasonCode.GI_RESCUE_NIL, minutes_into_rescue=target.offset_min))
        return _Dose(
            0.0,
            RESCUE_SIP_FLUID_ML,
            RESCUE_SIP_SODIUM_MG,
            FuelForm.rescue_sip,
            gentle_left,
            "GI Rescue: no calories yet. Take a small water and salt sip only.",
            reasons,
            drops,
        )
    if target.kind == "rescue_ease":
        if zone == DigestiveZone.red:
            reasons.append(make_reason(ReasonCode.GI_RESCUE_RED, grade_percent=grade_percent))
            # The ease dose would have been 12 g. Red skips those calories.
            drops.append((RESCUE_EASE_CARBS_G, ReasonCode.GI_RESCUE_RED, {"grade_percent": float(grade_percent)}))
            return _Dose(
                0.0,
                RESCUE_SIP_FLUID_ML,
                RESCUE_SIP_SODIUM_MG,
                FuelForm.rescue_sip,
                gentle_left,
                f"GI Rescue: grade {grade_text} is still red, so skip calories and sip.",
                reasons,
                drops,
            )
        reasons.append(make_reason(ReasonCode.GI_RESCUE_EASE, carbs_g=RESCUE_EASE_CARBS_G))
        return _Dose(
            RESCUE_EASE_CARBS_G,
            RESCUE_EASE_FLUID_ML,
            RESCUE_EASE_SODIUM_MG,
            FuelForm.liquid,
            gentle_left,
            f"GI Rescue: {RESCUE_EASE_CARBS_G:.0f} g of liquid carb while the stomach settles.",
            reasons,
            drops,
        )

    if zone == DigestiveZone.green:
        carbs = base_carbs
        form = FuelForm.gel
        reason = _green_reason(shift_min, grade_text, carbs)
    elif zone == DigestiveZone.yellow:
        carbs = base_carbs * 0.5
        form = FuelForm.liquid
        reason = _yellow_reason(shift_min, grade_text, carbs)
        reasons.append(make_reason(ReasonCode.YELLOW_HALF, grade_percent=grade_percent, carbs_g=carbs))
        _remember_drop(drops, base_carbs - carbs, ReasonCode.YELLOW_HALF, grade_percent=grade_percent)
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
        reasons.append(
            make_reason(
                ReasonCode.SKIPPED_RED_ZONE,
                grade_percent=grade_percent,
                carbs_g=carbs,
                forfeited_g=forfeited,
            )
        )
        _remember_drop(drops, forfeited, ReasonCode.SKIPPED_RED_ZONE, grade_percent=grade_percent)

    if gentle_left > 0 and carbs > 0:
        before = carbs
        carbs *= 0.5
        gentle_left -= 1
        reason += " Half-size while GI Rescue eases off."
        reasons.append(make_reason(ReasonCode.GI_RESCUE_HALF, carbs_g=carbs))
        _remember_drop(drops, before - carbs, ReasonCode.GI_RESCUE_HALF)

    return _Dose(carbs, base_fluid, base_sodium, form, gentle_left, reason, reasons, drops)


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




def _savoury_minute(athlete: AthleteInput, finish_min: float) -> float:
    """When green feeds should leave the sweet rotation.

    Savoury rotation starts at 2.5 hours (150 minutes), or at 1.5 hours
    (90 minutes) when the early savoury preference is on. A race longer
    than 3.5 hours compares those marks with 3.5 hours and halfway, then
    keeps the earlier one. Both starting marks are already earlier, so
    that comparison does not move the switch.
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
        dose.reasons.append(make_reason(ReasonCode.PORTION, carbs_g=dose.carbs_g))
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
        return _climb_without_gel(dose, athlete, zone, grade_percent)

    foods = _resolved_foods(athlete.vest_foods, VEST_MENU, athlete.custom_vest_items)
    if not _gels_selected(athlete):
        foods = [item for item in foods if item.kind != "gel" and item.key not in GEL_VEST_KEYS]
    elif not foods:
        foods = [VEST_MENU["gel_25"]]
    if not foods:
        removed = dose.carbs_g
        dose.form = FuelForm.sip
        dose.carbs_g = 0
        dose.reason += " No gel is in the vest, and no other vest food is selected."
        dose.reasons.append(make_reason(ReasonCode.NO_VEST_FOOD, forfeited_g=removed))
        _remember_drop(dose.drops, removed, ReasonCode.NO_VEST_FOOD, forfeited_g=removed)
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
            picked = pick(small[:1])
            dose.reasons.append(make_reason(ReasonCode.OPENING_SOLID, carbs_g=dose.carbs_g))
            return picked
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
                picked = pick(solids)
                dose.reasons.append(
                    make_reason(
                        ReasonCode.SAVOURY_SLOT,
                        switch_min=_savoury_minute(athlete, finish_min),
                        carbs_g=dose.carbs_g,
                    )
                )
                return picked
        else:
            between = [item for item in foods if item.kind == "solid" and item.key != "sandwich"]
            if between:
                clock.last_solid_min = when
                picked = pick(between)
                dose.reasons.append(make_reason(ReasonCode.SOLID_SLOT, carbs_g=dose.carbs_g))
                return picked
    return pick(easy or foods[:1])


def _climb_without_gel(
    dose: _Dose,
    athlete: AthleteInput,
    zone: DigestiveZone,
    grade_percent: float,
) -> tuple[str, str]:
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
        removed = dose.carbs_g
        dose.form = FuelForm.sip
        dose.carbs_g = 0
        dose.reason += " No gel or drink mix in the vest. Sip water and salt on this climb."
        dose.reasons.append(
            make_reason(ReasonCode.NO_CLIMB_FOOD, grade_percent=grade_percent, forfeited_g=removed)
        )
        _remember_drop(
            dose.drops,
            removed,
            ReasonCode.NO_CLIMB_FOOD,
            grade_percent=grade_percent,
            forfeited_g=removed,
        )
        return "", ""
    dose.form = FuelForm.micro_dose if zone is DigestiveZone.red else FuelForm.real_food
    dose.reason += f" No gel or drink mix. Take tiny bites of {soft.name}."
    return soft.name, soft.flavor


def _wants_caffeine(
    segments: list[Segment],
    when: float,
    race_start: float,
    finish_min: float,
    *,
    start_time_minutes: int | None = None,
) -> bool:
    """True during overnight circadian lows, before a steep climb, or past 45% of the race.

    The race clock runs from the start, so halfway is half of the finish
    minute. When a race start time is known and the runner is moving through
    the overnight dip (01:00 to 05:30) after the opening 90 minutes, caffeine
    is unlocked earlier to counter drowsiness.
    """

    span = max(1.0, finish_min - race_start)
    overnight_low = False
    if start_time_minutes is not None and when >= race_start + 90.0:
        clock_min = (start_time_minutes + when) % 1440.0
        overnight_low = 60.0 <= clock_min <= 330.0
    if not overnight_low and when < race_start + 0.45 * span and race_start < finish_min * 0.45:
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
    wants = _wants_caffeine(segments, when, race_start, finish_min, start_time_minutes=athlete.start_time_minutes)
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
