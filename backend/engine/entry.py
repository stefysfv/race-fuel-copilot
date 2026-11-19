"""Dict in, JSON-ready dict out. No files, no network, no FastAPI."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from backend.engine.recalibrate import recalibrate, snap_custom_aids
from backend.engine.scheduler import generate_plan
from backend.engine.timing import flat_pace_for_target
from backend.engine.zones import preview_course_zones
from backend.gpx_parser import parse_gpx_xml, prepare_course, slice_segments
from backend.models import AidStation, GeneratePlanRequest, Race, RecalibrateRequest


def _bad_request(exc: ValidationError) -> ValueError:
    """Turn a Pydantic error into one sentence a caller can show."""

    parts: list[str] = []
    for error in exc.errors():
        where = ".".join(str(item) for item in error.get("loc", ()))
        message = str(error.get("msg") or "invalid value")
        parts.append(f"{where}: {message}" if where else message)
    return ValueError("; ".join(parts) or "Invalid request")


def _require_dict(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    return value


def _find_race(race_catalog: dict[str, Any], race_id: str) -> Race:
    races = race_catalog.get("races")
    if not isinstance(races, list):
        raise ValueError("race_catalog must contain a races list")
    for item in races:
        if isinstance(item, dict) and item.get("id") == race_id:
            try:
                return Race.model_validate(item)
            except ValidationError as exc:
                raise _bad_request(exc) from exc
    raise ValueError(f"Unknown race_id '{race_id}'")


def _merge_aids(catalog: list[AidStation], custom: list[AidStation]) -> list[AidStation]:
    """Keep catalog stations, then add typed ones that do not reuse a name."""

    taken = {station.name.casefold() for station in catalog}
    return [*catalog, *[station for station in custom if station.name.casefold() not in taken]]


def _profile_and_stations(
    race_id: str | None,
    gpx_xml: str | None,
    gpx_text: str | None,
    aid_stations: list,
    race_catalog: dict[str, Any],
) -> tuple[str, str, float, list[AidStation], list]:
    """Resolve a catalog race or an uploaded track. GPX text is already in memory."""

    if race_id and race_id.strip():
        race = _find_race(race_catalog, race_id.strip())
        if gpx_text and gpx_text.strip():
            profile = parse_gpx_xml(gpx_text)
        elif len(race.profile) >= 2:
            profile = list(race.profile)
        else:
            raise ValueError(f"Race '{race.id}' needs GPX text")
        course = prepare_course(profile)
        custom = snap_custom_aids(course, aid_stations)
        return race.id, race.name, race.distance_km, _merge_aids(race.aid_stations, custom), profile

    document = gpx_text if gpx_text is not None else (gpx_xml or "")
    profile = parse_gpx_xml(document)
    course = prepare_course(profile)
    return "uploaded", "Uploaded GPX", 0.0, snap_custom_aids(course, aid_stations), profile


def generate_plan_json(request: dict, race_catalog: dict, gpx_text: str | None) -> dict:
    """Build a plan from a request object, a race catalog object, and GPX text."""

    body = _require_dict(request, "request")
    catalog = _require_dict(race_catalog, "race_catalog")
    if gpx_text is not None and not isinstance(gpx_text, str):
        raise ValueError("gpx_text must be a string or null")
    try:
        parsed = GeneratePlanRequest.model_validate(body)
    except ValidationError as exc:
        raise _bad_request(exc) from exc
    race_id, race_name, distance_km, stations, profile = _profile_and_stations(
        parsed.race_id,
        parsed.gpx_xml,
        gpx_text,
        parsed.aid_stations,
        catalog,
    )
    athlete = parsed.athlete
    if parsed.match_target_time and athlete.target_time_minutes is not None:
        pace = flat_pace_for_target(profile, athlete.target_time_minutes)
        athlete = athlete.model_copy(update={"flat_pace_min_per_km": pace})
    plan = generate_plan(
        race_id=race_id,
        race_name=race_name,
        declared_distance_km=distance_km,
        aid_stations=stations,
        profile=profile,
        athlete=athlete,
        packed_vest_items=parsed.packed_vest_items,
    )
    return plan.model_dump(mode="json")


def recalibrate_json(request: dict, race_catalog: dict, gpx_text: str | None) -> dict:
    """Snap one fix and return the remaining plan. Inputs are plain dicts."""

    body = _require_dict(request, "request")
    catalog = _require_dict(race_catalog, "race_catalog")
    if gpx_text is not None and not isinstance(gpx_text, str):
        raise ValueError("gpx_text must be a string or null")
    try:
        parsed = RecalibrateRequest.model_validate(body)
    except ValidationError as exc:
        raise _bad_request(exc) from exc
    race_id, race_name, distance_km, stations, profile = _profile_and_stations(
        parsed.race_id,
        parsed.gpx_xml,
        gpx_text,
        parsed.aid_stations,
        catalog,
    )
    updated = recalibrate(
        race_id=race_id,
        race_name=race_name,
        declared_distance_km=distance_km,
        aid_stations=stations,
        profile=profile,
        athlete=parsed.athlete,
        latitude=parsed.latitude,
        longitude=parsed.longitude,
        elapsed_min=parsed.elapsed_min,
        gi_rescue=parsed.gi_rescue,
        packed_vest_items=parsed.packed_vest_items,
    )
    return updated.model_dump(mode="json")


def preview_zones_json(request: dict, race_catalog: dict, gpx_text: str | None) -> dict:
    """Classify the course into digestive zones. No stops, packs, or totals.

    The body is the same plan request the full builder accepts. Sliders call
    this so the red-share readout does not wait on a fueling plan.
    """

    body = _require_dict(request, "request")
    catalog = _require_dict(race_catalog, "race_catalog")
    if gpx_text is not None and not isinstance(gpx_text, str):
        raise ValueError("gpx_text must be a string or null")
    try:
        parsed = GeneratePlanRequest.model_validate(body)
    except ValidationError as exc:
        raise _bad_request(exc) from exc
    _race_id, _race_name, _distance_km, _stations, profile = _profile_and_stations(
        parsed.race_id,
        parsed.gpx_xml,
        gpx_text,
        parsed.aid_stations,
        catalog,
    )
    return preview_course_zones(slice_segments(prepare_course(profile)), parsed.athlete)
