"""The one-bite plan for a race of two hours or less."""

from __future__ import annotations

from dataclasses import dataclass

from backend.gpx_parser import CoursePoint

from backend.models import (
    AidLeg,
    AthleteInput,
    FuelForm,
    FuelStop,
    PackItem,
    Segment,
)

from backend.engine.constants import (
    FLASK_ML,
    HYDRATION_SIP_MIN,
    SHORT_RACE_MAX_MIN,
    SHORT_RACE_NOTE,
    SHORT_RACE_SIP,
)

from backend.engine.doses_menu import (
    VEST_MENU,
    _gels_selected,
    _resolved_foods,
)

from backend.engine.timing import _stop_on_clock


def _is_short_race(athlete: AthleteInput) -> bool:
    """Glycogen lasts a race of two hours or less. The target clock decides."""

    target = athlete.target_time_minutes
    return target is not None and target <= SHORT_RACE_MAX_MIN














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
