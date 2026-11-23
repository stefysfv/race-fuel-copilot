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
    SOLID_EVERY_MIN,
    _REPEAT_PENALTY,
    _ZONE_FIT_BONUS,
)

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
        if abs(minute - when) < SOLID_EVERY_MIN - 0.05:
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
        score = ratio * 2.0 + _zone_fit_bonus(stock.item, zone) - penalty
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
    stop.sodium_mg = float(item.sodium_mg)
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
    return f"Vest Inventory Used: {' Â· '.join(parts)} ({left} left at finish)"


def _boost_aid_stations(
    stops: list[FuelStop],
    legs: list[AidLeg],
    athlete: AthleteInput,
    extra_g: float,
) -> float:
    """Use selected aid-station foods (up to 90 g/stop) to cover a vest shortfall."""

    if extra_g < 10.0:
        return extra_g
    station_stops = [stop for stop in stops if stop.at_aid_station and stop.carbs_g > 0]
    if not station_stops:
        return extra_g

    chosen = _resolved_foods(athlete.station_foods, STATION_MENU, athlete.custom_station_items)
    if not chosen:
        chosen = list(STATION_MENU.values())
    ranked = sorted(
        (item for item in chosen if item.carbs_g >= 5),
        key=lambda item: (-item.carbs_g, item.name),
    )
    if not ranked:
        ranked = sorted(chosen, key=lambda item: (-item.carbs_g, item.name))

    remaining = extra_g
    for idx, stop in enumerate(station_stops):
        if remaining < 10.0:
            break
        leg = next(
            (item for item in legs if abs(item.to_km - stop.distance_km) < 0.05 and item.station_carbs_g > 0),
            None,
        )
        leg_room = (leg.carbs_g - leg.station_carbs_g) if leg is not None else 90.0
        max_boost = min(90.0 - stop.carbs_g, remaining, leg_room)
        if max_boost < 10.0:
            continue

        target = stop.carbs_g + max_boost
        picks: dict[str, tuple[str, float, float, int]] = {}
        total_carbs = 0.0
        total_sodium = 0.0
        servings = 0

        # Pass 1: distinct high-carb items from the selected station menu
        for item in ranked:
            if servings >= 4 or total_carbs >= target - 5.0:
                break
            if total_carbs + item.carbs_g <= target + 1e-6:
                picks[item.key] = (item.name, item.carbs_g, item.sodium_mg, 1)
                total_carbs += item.carbs_g
                total_sodium += item.sodium_mg
                servings += 1

        # Pass 2: allow a second serving of the top item(s) if still short
        for item in ranked:
            if servings >= 4 or total_carbs >= target - 5.0:
                break
            if total_carbs + item.carbs_g <= target + 1e-6:
                if item.key in picks:
                    name, c, s, qty = picks[item.key]
                    picks[item.key] = (name, c, s, qty + 1)
                else:
                    picks[item.key] = (item.name, item.carbs_g, item.sodium_mg, 1)
                total_carbs += item.carbs_g
                total_sodium += item.sodium_mg
                servings += 1

        if total_carbs <= stop.carbs_g + 1.0:
            continue

        parts = [
            f"{qty}Ã— {name}" if qty > 1 else name
            for name, _c, _s, qty in picks.values()
        ]
        label = f"{' + '.join(parts)} ({total_carbs:.0f}g)"
        gained = total_carbs - stop.carbs_g
        stop.carbs_g = total_carbs
        stop.sodium_mg = total_sodium
        stop.reason = f"{label} â€” extra station food to cover vest shortfall"
        stop.reasons = [make_reason(ReasonCode.AID_RESTOCK, plate_carbs_g=total_carbs)]
        if leg is not None:
            leg.station_carbs_g = total_carbs
            leg.station_meal = label
            if leg.station_carbs_g + leg.carryover_carbs_g > leg.carbs_g:
                leg.carryover_carbs_g = max(0.0, leg.carbs_g - leg.station_carbs_g)
            leg.vest_carbs_g = max(0.0, leg.carbs_g - leg.station_carbs_g - leg.carryover_carbs_g)
        remaining = max(0.0, remaining - gained)
    return remaining


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

    Never schedules trail carbs beyond what is packed in the vest. Aid station
    plates and 0 g hydration sips stay put; unbacked trail slots are removed.
    Solids stay off red climbs, 90 minutes apart, and savoury solids wait for
    the palate switch. Caffeinated gels wait for green/yellow ground whenever
    non-red slots remain ahead, and total caffeine stops at 5 mg/kg.
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
    caf_start = (
        segments[0].start_minute + 0.45 * (segments[-1].end_minute - segments[0].start_minute)
        if segments
        else 0.0
    )
    savoury_at = _savoury_minute(athlete, finish_min)
    caffeine_cap = athlete.weight_kg * CAFFEINE_MG_PER_KG
    caffeine_used = sum(stop.caffeine_mg for stop in stops if not _is_vest_slot(stop))
    solid_minutes: list[float] = []
    stamped_ids: set[int] = set()

    def take(stop: FuelStop, *, later_non_red: bool = False) -> bool:
        nonlocal placed_g, last_id, caffeine_used
        leg_index = _leg_index_for_km(legs, stop.distance_km)
        blocked: set[str] = set()
        if segments and stop.scheduled_minute < caf_start:
            blocked |= {stock.item.id for stock in stocks if stock.item.caffeine_mg > 0}
            if blocked and all(stock.remaining <= 0 or stock.item.id in blocked for stock in stocks):
                blocked = set()
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
        )
        if choice is None:
            return False
        room = max(0.0, caffeine_cap - caffeine_used)
        delivered, form, caffeine = _packed_dose(stop, choice.item, room)
        _stamp_packed(stop, choice.item, carbs_g=delivered, caffeine_mg=caffeine, form=form)
        caffeine_used += caffeine
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
            continue
        later_non_red = any(
            _is_vest_slot(later) and later.zone is not DigestiveZone.red
            for later in stops[index + 1:]
        )
        if not take(stop, later_non_red=later_non_red):
            extra_g += stop.carbs_g

    # Remove every unbacked trail vest slot so the schedule never invents food.
    stops[:] = [
        stop
        for stop in stops
        if not _is_vest_slot(stop) or id(stop) in stamped_ids
    ]

    if consume_all and segments and any(stock.remaining > 0 for stock in stocks):
        quiet_limit = segments[0].start_minute + PRE_RACE_QUIET_MIN
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
                    detail=f"{item.carbs_g:.0f} g carbs each Â· from the packed vest",
                )
            )
        leg.pack = kept

    if extra_g >= 10.0 and all(stock.remaining <= 0 for stock in stocks):
        extra_g = _boost_aid_stations(stops, legs, athlete, extra_g)

    note = ""
    if extra_g >= 1 and all(stock.remaining <= 0 for stock in stocks):
        note = (
            f"Packed vest 100% scheduled ({int(round(placed_g))} g) Â· "
            f"+{int(round(extra_g))} g short of target â€” pack more food or add aid stations"
        )
    return _inventory_summary(stocks), note



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
    cap = FLASK_CAP_LONG if leg_hours > LONG_LEG_HOURS else FLASK_CAP
    if flask_limit is not None:
        cap = min(cap, flask_limit)
    if cap <= 0:
        return None
    needed = math.ceil(fluid_ml / FLASK_ML)
    carry = min(needed, cap)
    if needed > carry:
        refills = needed - carry
        note = (
            f"Refill {refills}Ã— at mid-leg fountain/stream if you need water"
            f" â€” up to {round(fluid_ml):,} ml on this leg (drink to thirst)."
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
        mix_note = f"1Ã— {drink.name} ({drink.carbs_g:.0f}g) in one flask."

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
