"""Vest depletion and the aid-leg packing list."""

from __future__ import annotations

from dataclasses import dataclass
import math

from backend.gpx_parser import (
    CoursePoint,
    location_at_distance,
)

from backend.models import (
    AidLeg,
    AidStation,
    AthleteInput,
    DigestiveZone,
    FuelForm,
    FuelStop,
    LockerItem,
    PackItem,
    PackedVestItem,
    ReasonCode,
    Segment,
)

from backend.engine.reasons import make_reason, shift_reasons

from backend.engine.coach_text import (
    _coach_note,
    _departure_has_food,
)

from backend.engine.constants import (
    AID_LEG_MIN_MINUTES,
    CAFFEINE_GAP_MIN,
    CAFFEINE_MG_PER_KG,
    CAPSULE_SODIUM_MG,
    CARRYOVER_WINDOW_MIN,
    DESCENT_LOCK_GRADE,
    FINISH_MERGE_M,
    FLASK_CAP,
    FLASK_CAP_LONG,
    FLASK_ML,
    GEL_CARBS_G,
    GEL_VEST_KEYS,
    LONG_LEG_HOURS,
    OPENING_EASY_MIN,
    PACK_BUFFER,
    PRE_RACE_FOOD_STATION_MIN,
    PRE_RACE_QUIET_MIN,
    PRE_RACE_SHORT_LEG_MIN,
    RED_MICRO_DOSE_G,
    SALT_STATION_KEYS,
    PACKED_SOLID_EVERY_MIN,
    SOLID_EVERY_MIN,
    _REPEAT_PENALTY,
    _ZONE_FIT_BONUS,
)

from backend.engine.food_library import lookup_food
from backend.engine.doses_menu import (
    STATION_MENU,
    VEST_MENU,
    _MenuItem,
    _gels_selected,
    _resolved_foods,
    _savoury_minute,
    station_plate,
)

from backend.engine.sweat import sweat_rates

from backend.engine.timing import (
    _average_grade,
    _distance_at_minute,
    _minute_at_distance,
    _place_feed,
)


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


def _is_counted_solid(stop: FuelStop) -> bool:
    """A real bite on the trail. Station plates are a meal, not a vest solid."""

    return stop.form is FuelForm.real_food and stop.carbs_g > 0 and not stop.at_aid_station


def _whole_pieces(remaining: float, portion: float) -> int:
    """How many whole portions fit in `remaining`.

    A custom portion can be a subnormal float, around 1e-308. Dividing by it
    is infinity, and `int(infinity)` raises OverflowError. That value is not
    a bite the vest can count, so the count is 0 instead of a crash.
    """

    if portion <= 0 or not math.isfinite(portion) or not math.isfinite(remaining):
        return 0
    raw = remaining // portion
    if not math.isfinite(raw) or raw < 1 or raw > 1_000_000:
        return 0
    return int(raw)


def _ceil_pieces(remaining: float, portion: float) -> int:
    """Round a portion count up, with the same guard as `_whole_pieces`."""

    if portion <= 0 or remaining <= 0 or not math.isfinite(portion) or not math.isfinite(remaining):
        return 0
    raw = remaining / portion
    if not math.isfinite(raw) or raw > 1_000_000:
        return 0
    return int(math.ceil(raw - 1e-12))


def _solid_stamp_blocked(
    item: PackedVestItem,
    stop: FuelStop,
    solid_minutes: list[float],
    savoury_at: float,
) -> bool:
    """True when this piece would break the solid gap or the savoury clock.

    Red ground is delivered as a micro-dose, so it is not a solid bite.
    Replacing a solid keeps that stop's own minute and checks the others.
    """

    if stop.zone is DigestiveZone.red or item.form != "solid":
        return False
    when = stop.scheduled_minute
    if item.flavor == "savoury" and when + 1e-6 < savoury_at:
        return True
    own = when if _is_counted_solid(stop) else None
    for minute in solid_minutes:
        if own is not None and abs(minute - own) <= 0.05:
            continue
        if abs(minute - when) < PACKED_SOLID_EVERY_MIN - 0.05:
            return True
    return False


def _packed_dose(
    stop: FuelStop,
    item: PackedVestItem,
    caffeine_room: float,
) -> tuple[float, FuelForm, float]:
    """Carbs, form, and caffeine this piece may deliver on this stop.

    A red window stays a micro-dose of at most 15 g, and it does not take
    caffeine. Anywhere else a gel may take caffeine only up to the room left
    under the 5 mg/kg cap.
    """

    if stop.zone is DigestiveZone.red:
        carbs = min(float(item.carbs_g), RED_MICRO_DOSE_G, float(stop.carbs_g))
        return carbs, FuelForm.micro_dose, 0.0
    caffeine = 0.0
    if item.form == "gel" and caffeine_room > 1e-6:
        caffeine = min(float(item.caffeine_mg), caffeine_room)
    return float(item.carbs_g), _packed_form(item), caffeine


def _note_solid(stop: FuelStop, was_solid: bool, solid_minutes: list[float]) -> None:
    """Keep the solid clock in step with a stop whose form just changed."""

    now = _is_counted_solid(stop)
    if was_solid and not now:
        solid_minutes[:] = [
            minute for minute in solid_minutes if abs(minute - stop.scheduled_minute) > 0.05
        ]
    elif now and not was_solid:
        solid_minutes.append(stop.scheduled_minute)


def _choose_packed(
    stocks: list[_VestStock],
    zone: DigestiveZone,
    last_id: str | None,
    used_in_leg: dict[str, int],
    placed_carbs: float,
    budget_g: float,
    consume_all: bool,
    blocked_ids: set[str] | None = None,
    stop: FuelStop | None = None,
    solid_minutes: list[float] | None = None,
    savoury_at: float = 0.0,
    temperature_c: float = 18.0,
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
        if stop is not None and _solid_stamp_blocked(stock.item, stop, solid_minutes or [], savoury_at):
            continue
        # A red stamp only delivers the micro-dose, so that is what it costs.
        cost = float(stock.item.carbs_g)
        if zone is DigestiveZone.red:
            cost = min(cost, RED_MICRO_DOSE_G)
        if not consume_all and placed_carbs + cost > budget_g + 0.5:
            continue
        ratio = stock.remaining / stock.initial
        others_left = any(
            other.remaining > 0 and other.item.id != stock.item.id for other in stocks
        )
        penalty = 0.0
        if last_id == stock.item.id or (used_in_leg.get(stock.item.id, 0) >= 2 and others_left):
            penalty = _REPEAT_PENALTY
        heat_penalty = 0.9 if (temperature_c >= 22.0 and "chocolate" in stock.item.name.lower()) else 0.0
        score = ratio * 2.0 + _zone_fit_bonus(stock.item, zone) - penalty - heat_penalty
        key = (score, ratio, -used_in_leg.get(stock.item.id, 0))
        if best_key is None or key > best_key:
            best = stock
            best_key = key
    return best


def _stamp_packed(
    stop: FuelStop,
    item: PackedVestItem,
    *,
    carbs_g: float,
    caffeine_mg: float,
    form: FuelForm,
) -> None:
    """Write one packed piece onto a stop, using the dose the rules allowed."""

    stop.product = item.name
    stop.carbs_g = carbs_g
    prof = lookup_food(item.id, item.name)
    stop.sodium_mg = float(item.sodium_mg if item.sodium_mg > 0 else (prof.sodium_mg if prof else 0.0))
    base_kcal = item.kcal if item.kcal > 0 else (prof.kcal if prof else 0.0)
    ratio = (carbs_g / float(item.carbs_g)) if item.carbs_g > 0 else 1.0
    stop.kcal = base_kcal * ratio if base_kcal > 0 else 0.0
    stop.magnesium_mg = float(item.magnesium_mg if item.magnesium_mg > 0 else (prof.magnesium_mg if prof else 0.0)) * ratio
    stop.potassium_mg = float(item.potassium_mg if item.potassium_mg > 0 else (prof.potassium_mg if prof else 0.0)) * ratio
    stop.caffeine_mg = caffeine_mg
    stop.form = form
    stop.flavor = "Savoury/Salty" if item.flavor == "savoury" else "Sweet"
    stop.reason = f"Packed vest: {item.name}"
    # The stamp replaces the window grams. Keep only the clock move.
    kept = [
        entry
        for entry in stop.reasons
        if entry.code in {ReasonCode.SHIFTED_EARLIER, ReasonCode.SHIFTED_LATER}
    ]
    stop.reasons = kept + [make_reason(ReasonCode.PACKED_VEST, carbs_g=carbs_g)]


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


def _boost_aid_stations(
    stops: list[FuelStop],
    legs: list[AidLeg],
    athlete: AthleteInput,
    extra_g: float,
    *,
    need_salt: bool = False,
) -> float:
    """Scale aid-station table meals moderately (up to 40-50 g/stop) with salty/electrolyte pairings
    and timed second-half magnesium when selected."""

    if extra_g < 8.0 and not need_salt:
        return extra_g
    station_stops = [stop for stop in stops if stop.at_aid_station and stop.carbs_g > 0]
    if not station_stops:
        return extra_g

    chosen = _resolved_foods(athlete.station_foods, STATION_MENU, athlete.custom_station_items)
    if not chosen:
        chosen = list(STATION_MENU.values())

    mag_items = [item for item in chosen if item.magnesium_mg >= 100.0]
    regular_items = [item for item in chosen if item.magnesium_mg < 100.0] or chosen

    def table_sort_key(item: _MenuItem) -> tuple[int, float, str]:
        heat_melt = 1 if (athlete.temperature_c >= 22.0 and (item.heat_sensitive or "chocolate" in item.name.lower())) else 0
        return (heat_melt, -item.carbs_g, item.name)

    ranked = sorted((item for item in regular_items if item.carbs_g >= 1), key=table_sort_key)
    if not ranked:
        ranked = sorted(regular_items, key=table_sort_key)
    salty_pool = sorted(
        (item for item in ranked if item.sodium_mg >= 150 or item.key in SALT_STATION_KEYS),
        key=lambda item: (-item.sodium_mg, -item.carbs_g, item.name),
    )

    sensitive = athlete.climb_sensitivity == "sensitive" or (
        athlete.green_max_grade is not None and athlete.green_max_grade <= 3.5
    )
    meal_cap = 40.0 if sensitive else 50.0
    prioritize_salt = need_salt or athlete.temperature_c >= 20.0
    mag_scheduled = False
    mag_target_idx = max(0, (len(station_stops) * 2) // 3 - 1) if len(station_stops) >= 2 else 0

    remaining = extra_g
    for stop_idx, stop in enumerate(station_stops):
        if remaining < 8.0 and not prioritize_salt and not (mag_items and not mag_scheduled):
            break
        leg = next(
            (item for item in legs if abs(item.to_km - stop.distance_km) < 0.05 and item.station_carbs_g > 0),
            None,
        )
        leg_room = (leg.carbs_g - leg.station_carbs_g) if leg is not None else meal_cap
        max_boost = min(meal_cap - stop.carbs_g, max(0.0, remaining), leg_room)
        want_mag_here = bool(mag_items and not mag_scheduled and stop.scheduled_minute >= 180.0 and stop_idx >= mag_target_idx)
        if max_boost < 8.0 and not (prioritize_salt and stop.sodium_mg < 250.0) and not want_mag_here:
            continue

        target = min(meal_cap, stop.carbs_g + max(0.0, max_boost))
        picks: dict[str, tuple[str, float, float, float, float, float, int]] = {}
        total_carbs = 0.0
        total_sodium = 0.0
        total_kcal = 0.0
        total_mag = 0.0
        total_pot = 0.0
        servings = 0

        def add_item(item: _MenuItem) -> None:
            nonlocal total_carbs, total_sodium, total_kcal, total_mag, total_pot, servings
            kcal = item.kcal if item.kcal > 0 else item.carbs_g * (4.0 if item.kind == "liquid" else 5.0)
            picks[item.key] = (item.name, item.carbs_g, item.sodium_mg, kcal, item.magnesium_mg, item.potassium_mg, 1)
            total_carbs += item.carbs_g
            total_sodium += item.sodium_mg
            total_kcal += kcal
            total_mag += item.magnesium_mg
            total_pot += item.potassium_mg
            servings += 1

        # 1) Seed a rotated salty/electrolyte anchor (Electrolyte drink, Salted cucumber, Sour pickles, Warm broth, Salted potatoes, Chips)
        if prioritize_salt and salty_pool:
            anchor = salty_pool[stop_idx % len(salty_pool)]
            if anchor.carbs_g <= target + 1e-6:
                add_item(anchor)

        # 2) Add timed second-half Magnesium shot/tab once when ticked on ultras >= 3h
        if want_mag_here and mag_items:
            mag = mag_items[0]
            if total_carbs + mag.carbs_g <= meal_cap + 5.0:
                add_item(mag)
                mag_scheduled = True

        # 3) Rotate carb items across stations for variety
        non_picked = [item for item in ranked if item.key not in picks]
        if len(non_picked) > 2:
            shift = stop_idx % min(4, len(non_picked))
            ordered = non_picked[shift:] + non_picked[:shift]
        else:
            ordered = non_picked

        for item in ordered:
            if servings >= 3 or total_carbs >= target - 3.0:
                break
            if total_carbs + item.carbs_g <= target + 1e-6:
                add_item(item)

        # 4) Top up with a low-carb salty bite (Salted cucumber, Sour pickles, Electrolyte drink) if sodium is still low
        if servings < 3 and prioritize_salt and total_sodium < 250.0:
            for item in salty_pool:
                if item.key in picks:
                    continue
                if total_carbs + item.carbs_g <= target + 1e-6:
                    add_item(item)
                    break

        improved_carbs = total_carbs > stop.carbs_g + 1.0
        improved_salt = prioritize_salt and total_carbs >= stop.carbs_g - 5.0 and total_sodium > stop.sodium_mg + 50.0
        added_mag = total_mag >= 100.0
        if not (improved_carbs or improved_salt or added_mag):
            continue

        parts = [
            f"{qty}× {name}" if qty > 1 else name
            for name, _c, _s, _k, _mg, _po, qty in picks.values()
        ]
        label = f"{' + '.join(parts)} ({total_carbs:.0f}g)"
        gained = max(0.0, total_carbs - stop.carbs_g)
        stop.carbs_g = total_carbs
        stop.sodium_mg = total_sodium
        stop.kcal = total_kcal
        stop.magnesium_mg = total_mag
        stop.potassium_mg = total_pot
        stop.reason = f"{label} — real food meal at aid station"
        stop.reasons = [make_reason(ReasonCode.AID_RESTOCK, plate_carbs_g=total_carbs)]
        if leg is not None:
            leg.station_carbs_g = total_carbs
            leg.station_meal = label
            if leg.station_carbs_g + leg.carryover_carbs_g > leg.carbs_g:
                leg.carryover_carbs_g = max(0.0, leg.carbs_g - leg.station_carbs_g)
            leg.vest_carbs_g = max(0.0, leg.carbs_g - leg.station_carbs_g - leg.carryover_carbs_g)
        remaining = max(0.0, remaining - gained)
    return remaining


def _restock_from_aid_stations(
    candidate_slots: list[FuelStop],
    stops: list[FuelStop],
    legs: list[AidLeg],
    athlete: AthleteInput,
    solid_minutes: list[float],
    savoury_at: float,
    quiet_limit: float,
    *,
    need_salt: bool = False,
) -> tuple[float, dict[int, list[tuple[str, float, int]]]]:
    """After a food aid station, grab portable station food for the next leg instead of inventing gels.

    Uses each item's ``portable`` and ``heat_sensitive`` flags from the food
    library so loose cups/wet items stay at the table and carryable foods
    (salted potatoes, pretzels, chips, sandwiches, bananas, dates, etc.) fill
    the trail carry list.
    """

    if not candidate_slots or len(legs) < 2:
        return 0.0, {}

    chosen = _resolved_foods(athlete.station_foods, STATION_MENU, athlete.custom_station_items)
    if not chosen:
        chosen = list(STATION_MENU.values())
    portable = [
        item
        for item in chosen
        if item.kind == "solid" and item.portable and item.carbs_g >= 10
    ]
    if not portable:
        portable = [item for item in chosen if item.kind == "solid" and item.carbs_g >= 10]
    if athlete.temperature_c >= 22.0 and any(not item.heat_sensitive for item in portable):
        portable = [item for item in portable if not item.heat_sensitive]

    food_legs = {
        idx + 1
        for idx, leg in enumerate(legs[:-1])
        if leg.station_carbs_g > 0
    }
    if not food_legs or not portable:
        return 0.0, {}

    window_g = athlete.carbs_g_per_hour * (athlete.feed_interval_min / 60.0)
    covered = 0.0
    per_leg_counts: dict[int, dict[str, tuple[str, float, int]]] = {}
    savoury_i = 0
    sweet_i = 0
    alt_i = 0

    for slot in candidate_slots:
        if slot.scheduled_minute < quiet_limit - 1e-6:
            continue
        if "GI Rescue" in slot.reason or slot.form is FuelForm.rescue_sip:
            continue
        if slot.zone is not DigestiveZone.green:
            continue
        leg_idx = _leg_index_for_km(legs, slot.distance_km)
        if leg_idx not in food_legs:
            continue
        if any(
            abs(minute - slot.scheduled_minute) < PACKED_SOLID_EVERY_MIN - 0.05
            for minute in solid_minutes
        ):
            continue

        eligible = [
            item
            for item in portable
            if slot.scheduled_minute + 1e-6 >= savoury_at
            or item.flavor != "Savoury/Salty"
        ]
        if not eligible:
            continue

        if slot.scheduled_minute + 1e-6 >= savoury_at:
            savoury_pool = [i for i in eligible if i.flavor == "Savoury/Salty"]
            if need_salt or athlete.temperature_c >= 20.0:
                savoury_pool.sort(key=lambda item: (-item.sodium_mg, -item.carbs_g, item.name))
            sweet_pool = [i for i in eligible if i.flavor != "Savoury/Salty"]
            if savoury_pool and sweet_pool:
                if alt_i % 2 == 0:
                    picked = savoury_pool[savoury_i % len(savoury_pool)]
                    savoury_i += 1
                else:
                    picked = sweet_pool[sweet_i % len(sweet_pool)]
                    sweet_i += 1
                alt_i += 1
            elif savoury_pool:
                picked = savoury_pool[savoury_i % len(savoury_pool)]
                savoury_i += 1
            else:
                picked = sweet_pool[sweet_i % len(sweet_pool)]
                sweet_i += 1
        else:
            picked = eligible[sweet_i % len(eligible)]
            sweet_i += 1

        carbs = min(float(picked.carbs_g), window_g)
        if carbs < 5.0:
            continue

        dep_name = legs[leg_idx].from_name
        kept = [
            entry
            for entry in slot.reasons
            if entry.code in {ReasonCode.SHIFTED_EARLIER, ReasonCode.SHIFTED_LATER}
        ]
        ratio = (carbs / float(picked.carbs_g)) if picked.carbs_g > 0 else 1.0
        slot.product = picked.name
        slot.carbs_g = carbs
        slot.sodium_mg = float(picked.sodium_mg) * ratio
        slot.kcal = (float(picked.kcal) * ratio) if picked.kcal > 0 else 0.0
        slot.magnesium_mg = float(picked.magnesium_mg) * ratio
        slot.potassium_mg = float(picked.potassium_mg) * ratio
        slot.caffeine_mg = 0.0
        slot.form = FuelForm.real_food
        slot.flavor = "Savoury/Salty" if picked.flavor == "Savoury/Salty" else "Sweet"
        slot.reason = f"Aid restock ({dep_name}): {picked.name}"
        slot.reasons = kept + [make_reason(ReasonCode.AID_RESTOCK, plate_carbs_g=carbs)]
        stops.append(slot)
        solid_minutes.append(slot.scheduled_minute)
        covered += carbs
        bucket = per_leg_counts.setdefault(leg_idx, {})
        if picked.key in bucket:
            n, c, q = bucket[picked.key]
            bucket[picked.key] = (n, c, q + 1)
        else:
            bucket[picked.key] = (picked.name, carbs, 1)

    summary = {
        leg_idx: list(bucket.values())
        for leg_idx, bucket in per_leg_counts.items()
    }
    return covered, summary



def _consume_packed_vest(
    stops: list[FuelStop],
    legs: list[AidLeg],
    segments: list[Segment],
    course: list[CoursePoint],
    items: list[PackedVestItem],
    feed_interval_min: int,
    athlete: AthleteInput,
) -> tuple[str, str]:
    """Spend the packed vest across the race as a hard inventory cap.

    Never invents trail gels beyond what is packed in the vest. Aid station
    plates and 0 g hydration sips stay put. Real-food solids space at 45 min
    on runnable ground, caffeinated gels spread evenly across the back half
    (or overnight circadian dip), and food aid stations supply moderate table
    meals plus portable trail food whenever the vest cannot cover a leg.
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
    finish_min = segments[-1].end_minute if segments else 0.0
    race_start = segments[0].start_minute if segments else 0.0
    quiet_limit = race_start + PRE_RACE_QUIET_MIN
    caf_start = race_start + 0.45 * (finish_min - race_start) if segments else 0.0
    savoury_at = _savoury_minute(athlete, finish_min)
    caffeine_cap = athlete.weight_kg * CAFFEINE_MG_PER_KG
    caffeine_used = sum(stop.caffeine_mg for stop in stops if not _is_vest_slot(stop))
    caffeine_minutes: list[float] = [
        stop.scheduled_minute
        for stop in stops
        if not _is_vest_slot(stop) and stop.caffeine_mg > 0
    ]
    total_caf_qty = sum(stock.initial for stock in stocks if stock.item.caffeine_mg > 0)
    caf_span = max(0.0, finish_min - caf_start)
    # Spread caffeinated gels across the back half instead of bunching them at the midpoint
    caf_gap = (
        max(CAFFEINE_GAP_MIN, min(210.0, caf_span / (total_caf_qty + 0.5)))
        if total_caf_qty >= 2 and caf_span > CAFFEINE_GAP_MIN
        else CAFFEINE_GAP_MIN
    )
    solid_minutes: list[float] = []
    stamped_ids: set[int] = set()
    unbacked_slots: list[FuelStop] = []

    def caffeine_unlocked(when: float) -> bool:
        if when >= caf_start:
            return True
        if athlete.start_time_minutes is not None and when >= race_start + 90.0:
            clock_min = (athlete.start_time_minutes + when) % 1440.0
            if 60.0 <= clock_min <= 330.0:
                return True
        return False

    def take(stop: FuelStop, *, later_non_red: bool = False) -> bool:
        nonlocal placed_g, last_id, caffeine_used
        leg_index = _leg_index_for_km(legs, stop.distance_km)
        blocked: set[str] = set()
        if segments and not caffeine_unlocked(stop.scheduled_minute):
            blocked |= {stock.item.id for stock in stocks if stock.item.caffeine_mg > 0}
            if blocked and all(stock.remaining <= 0 or stock.item.id in blocked for stock in stocks):
                # Keep saving caffeinated gels for the back half if food aid stations can cover early legs
                has_food_stations = any(leg.station_carbs_g > 0 for leg in legs)
                if not has_food_stations:
                    blocked = set()
        if any(
            abs(minute - stop.scheduled_minute) < caf_gap - 0.05
            for minute in caffeine_minutes
        ):
            blocked |= {stock.item.id for stock in stocks if stock.item.caffeine_mg > 0}
        if stop.zone is DigestiveZone.red:
            blocked |= {stock.item.id for stock in stocks if stock.item.form == "solid"}
            if later_non_red:
                blocked |= {stock.item.id for stock in stocks if stock.item.caffeine_mg > 0}
        choice = _choose_packed(
            stocks,
            stop.zone,
            last_id,
            used_in_leg[leg_index],
            placed_g,
            budget_g,
            consume_all,
            blocked,
            stop,
            solid_minutes,
            savoury_at,
            athlete.temperature_c,
        )
        if choice is None:
            return False
        room = max(0.0, caffeine_cap - caffeine_used)
        delivered, form, caffeine = _packed_dose(stop, choice.item, room)
        _stamp_packed(stop, choice.item, carbs_g=delivered, caffeine_mg=caffeine, form=form)
        caffeine_used += caffeine
        if caffeine > 0:
            caffeine_minutes.append(stop.scheduled_minute)
        choice.remaining -= 1
        placed_g += delivered
        used_in_leg[leg_index][choice.item.id] = used_in_leg[leg_index].get(choice.item.id, 0) + 1
        last_id = choice.item.id
        if _is_counted_solid(stop):
            solid_minutes.append(stop.scheduled_minute)
        stamped_ids.add(id(stop))
        return True

    for index, stop in enumerate(stops):
        if not _is_vest_slot(stop):
            continue
        if all(stock.remaining <= 0 for stock in stocks):
            extra_g += stop.carbs_g
            unbacked_slots.append(stop)
            continue
        later_non_red = any(
            _is_vest_slot(later) and later.zone is not DigestiveZone.red
            for later in stops[index + 1:]
        )
        if not take(stop, later_non_red=later_non_red):
            extra_g += stop.carbs_g
            unbacked_slots.append(stop)

    # Remove every unbacked trail vest slot so the schedule never invents gels.
    stops[:] = [
        stop
        for stop in stops
        if not _is_vest_slot(stop) or id(stop) in stamped_ids
    ]

    if consume_all and segments and any(stock.remaining > 0 for stock in stocks):
        minute = float(feed_interval_min)
        while minute <= finish_min - 10 and any(stock.remaining > 0 for stock in stocks):
            placed = None
            for shift in (12.0, -12.0, 0.0):
                candidate = minute + shift
                if candidate < quiet_limit:
                    continue
                if any(abs(stop.scheduled_minute - candidate) < 8 for stop in stops):
                    continue
                placed = _place_feed(segments, candidate, prefer_safe=True)
                if placed is not None and placed[1] >= quiet_limit - 1e-6:
                    minute = candidate
                    break
                placed = None
            if placed is not None:
                segment, when = placed
                if when >= quiet_limit - 1e-6 and not any(
                    abs(stop.scheduled_minute - when) < 8 for stop in stops
                ):
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
                        reasons=shift_reasons(minute, when, segment.grade_percent, segment.index),
                    )
                    if take(extra_stop, later_non_red=False):
                        stops.append(extra_stop)
                        extra_g = max(0.0, extra_g - extra_stop.carbs_g)
            minute += feed_interval_min

    # Whenever the starting vest leaves trail slots uncovered, use food aid stations:
    # 1) Moderate real-food table meals (capped at 40-50 g/stop so the stomach stays steady)
    # 2) Portable carryable station snacks on the trail legs after each food station
    has_electrolytes = athlete.has_salt_caps or any(
        stock.item.form == "liquid" and stock.item.sodium_mg > 0 for stock in stocks
    )
    need_salt = not has_electrolytes

    # Plain water sips carry 0 mg sodium when neither drink mix nor salt caps are packed
    if not has_electrolytes:
        for stop in stops:
            if stop.form is FuelForm.sip and stop.carbs_g == 0 and not stop.at_aid_station:
                stop.sodium_mg = 0.0

    restock_pack: dict[int, list[tuple[str, float, int]]] = {}
    if (extra_g >= 8.0 or need_salt) and (all(stock.remaining <= 0 for stock in stocks) or packed_g < budget_g * 0.95):
        extra_g = _boost_aid_stations(stops, legs, athlete, extra_g, need_salt=need_salt)
        if unbacked_slots and extra_g >= 8.0:
            restocked_g, restock_pack = _restock_from_aid_stations(
                unbacked_slots,
                stops,
                legs,
                athlete,
                solid_minutes,
                savoury_at,
                quiet_limit,
                need_salt=need_salt,
            )
            extra_g = max(0.0, extra_g - restocked_g)

    stops.sort(key=lambda stop: (stop.scheduled_minute, stop.sequence))
    for index, stop in enumerate(stops, start=1):
        stop.sequence = index

    for leg_idx, leg in enumerate(legs):
        kept = [line for line in leg.pack if _keep_pack_line(line)]
        counts: dict[str, int] = {}
        for stop in stops:
            if not stop.reason.startswith("Packed vest:"):
                continue
            if _leg_index_for_km(legs, stop.distance_km) != leg_idx:
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
        for name, carbs_each, quantity in restock_pack.get(leg_idx, []):
            kept.append(
                PackItem(
                    item=name,
                    quantity=quantity,
                    detail=f"{carbs_each:.0f} g carbs each · grab from {leg.from_name} aid station",
                )
            )
        leg.pack = kept

    note = ""
    if extra_g >= 1 and all(stock.remaining <= 0 for stock in stocks):
        has_food_stations = any(leg.station_carbs_g > 0 for leg in legs)
        if has_food_stations:
            note = (
                f"Packed vest 100% scheduled ({int(round(placed_g))} g) · "
                f"+{int(round(extra_g))} g still short after aid station food — pack extra carbs or drink mix"
            )
        else:
            note = (
                f"Packed vest 100% scheduled ({int(round(placed_g))} g) · "
                f"no food aid stations added (+{int(round(extra_g))} g short) — add aid stations in Setup or pack more food"
            )
    return _inventory_summary(stocks), note




def _apply_aid_only_fueling(
    stops: list[FuelStop],
    legs: list[AidLeg],
    segments: list[Segment],
    athlete: AthleteInput,
) -> None:
    """When the runner carries an empty vest (0 g) but has food aid stations,
    zero ghost sodium on plain water sips, boost station meals moderately, and
    restock portable station food onto green trail slots after each food station."""

    if not legs:
        return
    if not athlete.has_salt_caps:
        for stop in stops:
            if stop.form is FuelForm.sip and stop.carbs_g == 0 and not stop.at_aid_station:
                stop.sodium_mg = 0.0

    has_food_stations = any(leg.station_carbs_g > 0 for leg in legs)
    if not has_food_stations:
        return

    finish_min = segments[-1].end_minute if segments else 0.0
    race_start = segments[0].start_minute if segments else 0.0
    quiet_limit = race_start + PRE_RACE_QUIET_MIN
    savoury_at = _savoury_minute(athlete, finish_min)
    need_salt = not athlete.has_salt_caps

    # Find empty-vest forfeited trail slots (carbs_g == 0, form == sip) after food stations
    candidates = [
        stop
        for stop in stops
        if not stop.at_aid_station
        and stop.form is FuelForm.sip
        and stop.carbs_g == 0
        and "GI Rescue" not in stop.reason
        and stop.scheduled_minute >= quiet_limit - 1e-6
    ]
    budget_g = sum(leg.carbs_g for leg in legs)
    current_g = sum(stop.carbs_g for stop in stops)
    extra_g = max(0.0, budget_g - current_g)

    extra_g = _boost_aid_stations(stops, legs, athlete, extra_g, need_salt=need_salt)
    if not candidates or extra_g < 8.0:
        return

    # Remove candidate slots before _restock_from_aid_stations re-appends the ones it fills
    cand_ids = {id(s) for s in candidates}
    kept_stops = [s for s in stops if id(s) not in cand_ids]
    solid_minutes: list[float] = []
    restocked_g, restock_pack = _restock_from_aid_stations(
        candidates,
        kept_stops,
        legs,
        athlete,
        solid_minutes,
        savoury_at,
        quiet_limit,
        need_salt=need_salt,
    )
    # Keep any unfilled sip slots as plain water sips so hydration reminders remain
    filled_ids = {id(s) for s in kept_stops}
    for s in candidates:
        if id(s) not in filled_ids:
            kept_stops.append(s)

    kept_stops.sort(key=lambda stop: (stop.scheduled_minute, stop.sequence))
    for index, stop in enumerate(kept_stops, start=1):
        stop.sequence = index
    stops[:] = kept_stops

    for leg_idx, leg in enumerate(legs):
        for name, carbs_each, quantity in restock_pack.get(leg_idx, []):
            leg.pack.append(
                PackItem(
                    item=name,
                    quantity=quantity,
                    detail=f"{carbs_each:.0f} g carbs each \u00b7 grab from {leg.from_name} aid station",
                )
            )


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
        # The plate at this station and the plate carried in from the last
        # one are both part of this leg. When both are large, shrink the
        # carryover so the three buckets match the leg budget. The plate
        # at the table stays.
        if station_carbs + cover > carbs_g + 1e-6:
            cover = max(0.0, carbs_g - station_carbs)
            trail = max(0.0, carbs_g - station_carbs - cover)
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
    if flask_limit is not None:
        cap = flask_limit
    else:
        cap = FLASK_CAP_LONG if leg_hours > LONG_LEG_HOURS else FLASK_CAP
    if cap <= 0:
        return None
    needed = math.ceil(fluid_ml / FLASK_ML)
    carry = min(needed, cap)
    if needed > carry:
        refills = needed - carry
        note = (
            f"Refill {refills}× at mid-leg fountain/stream if you need water"
            f" — up to {round(fluid_ml):,} ml on this leg (drink to thirst)."
        )
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
        quantity = min(slots, _whole_pieces(remaining, solid.carbs_g))
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
        quantity = _ceil_pieces(remaining, gel.carbs_g)
        if quantity >= 1:
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
        quantity = _ceil_pieces(remaining, plain.carbs_g)
        if quantity < 1:
            plain = None
    if plain is not None and remaining >= 1:
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
