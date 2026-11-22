"""Fluid copy is a ceiling, not an order to drink a fixed volume.

The scanner reads UI sources and engine message strings. It does not read
``backend/engine/sweat.py``: that file is the formula, documented in
``docs/physiology-assumptions.md``, and it is not shown to the athlete.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.engine.scheduler import generate_plan
from backend.models import AthleteInput, TrackPoint

ROOT = Path(__file__).resolve().parents[1]

# Athlete-facing copy. Vendor bundles and tests are not the product voice.
UI_FILES = (
    "frontend/index.html",
    "frontend/packing.js",
    "frontend/app.js",
    "frontend/live.js",
    "frontend/leg_projection.js",
    "frontend/reason_strings.js",
    "frontend/fuel_budget.js",
    "frontend/offline_engine.js",
    "frontend/food_menu.js",
    "frontend/plan_request.js",
    "frontend/zone_profile.js",
)
ENGINE_FILES = (
    "backend/engine/packing.py",
    "backend/engine/coach_text.py",
    "backend/engine/doses_menu.py",
    "backend/engine/short_race.py",
    "backend/engine/constants.py",
    "backend/engine/recalibrate.py",
    "backend/engine/totals_warnings.py",
    "backend/engine/scheduler.py",
)

_LITERAL = re.compile(
    r'"""(.*?)"""|\'\'\'(.*?)\'\'\'|`((?:\\.|[^`\\])*)`|"((?:\\.|[^"\\])*)"|\'((?:\\.|[^\'\\])*)\'',
    re.S,
)
_ML = re.compile(r"ml", re.I)
_RATE = re.compile(r"ml\s*/\s*h", re.I)
_TOTAL = re.compile(r"\btotal\b", re.I)
# Hyphenated tokens such as drink-line and drink-carbs are labels, not verbs.
_COMMAND = re.compile(r"(?<![\w-])(drink|consume|finish)(?![\w-])", re.I)
_CEILING = re.compile(r"up to", re.I)
_THIRST = re.compile(r"thirst", re.I)
_EQUIPMENT = re.compile(r"flask|per 500", re.I)
_FIXED_VOLUME = re.compile(r"\b(?:drink|consume|finish)\s+\d[\d,]*\s*ml\b", re.I)


def _is_fixed_fluid_instruction(line: str) -> bool:
    """True when a line tells the athlete to drink a named volume."""

    if not _ML.search(line):
        return False
    if _FIXED_VOLUME.search(line):
        return True
    if _EQUIPMENT.search(line) and not any(pattern.search(line) for pattern in (_RATE, _TOTAL, _COMMAND)):
        return False
    if _CEILING.search(line) and _THIRST.search(line):
        return False
    if _RATE.search(line) or _TOTAL.search(line) or _COMMAND.search(line):
        return True
    return bool(re.search(r"fluid", line, re.I) and re.search(r"\d|\{", line))


def _literals(text: str) -> list[str]:
    found: list[str] = []
    for match in _LITERAL.finditer(text):
        found.append(next(group for group in match.groups() if group is not None))
    return found


def _offending_lines(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    chunks = _literals(text)
    if path.suffix == ".html":
        chunks.append(text)
    problems: list[str] = []
    for chunk in chunks:
        for line in chunk.splitlines():
            if _is_fixed_fluid_instruction(line):
                problems.append(f"{path.as_posix()}: {line.strip()}")
    return problems


def test_ui_and_engine_messages_do_not_order_a_fixed_fluid_volume() -> None:
    problems: list[str] = []
    for relative in UI_FILES + ENGINE_FILES:
        problems.extend(_offending_lines(ROOT / relative))
    assert problems == []


def test_fluid_copy_says_ceiling_thirst_and_not_clinical_advice() -> None:
    page = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    packing = (ROOT / "frontend/packing.js").read_text(encoding="utf-8")
    engine = (ROOT / "backend/engine/packing.py").read_text(encoding="utf-8")

    assert "This plan is guidance, not medical or dietetic advice." in page
    assert "up to ${grouped(fluidRate)} ml/h" in packing
    assert "drink to thirst" in packing
    assert "typical range, adjust for sweat saltiness" in packing
    assert "long, cool race" in packing
    assert "dilute blood sodium" in packing
    assert "drink to thirst" in engine
    assert "total {round(fluid_ml)" not in engine


def test_built_plan_flask_note_is_a_ceiling() -> None:
    """A long leg still names a refill, and the sentence is not a quota."""

    points = _flat_course_m(30_000)
    plan = generate_plan(
        race_id="fluid-wording",
        race_name="Fluid wording",
        declared_distance_km=30.0,
        aid_stations=[],
        profile=points,
        athlete=AthleteInput(flat_pace_min_per_km=11.0, weight_kg=70, temperature_c=12),
    )
    messages: list[str] = list(plan.warnings)
    for leg in plan.aid_legs:
        messages.append(leg.note)
        for item in leg.pack:
            messages.append(item.detail)
    for stop in plan.fuel_stops:
        messages.append(stop.reason)
    text = "\n".join(messages)
    assert "drink to thirst" in text
    assert "up to" in text
    for line in text.splitlines():
        assert not _is_fixed_fluid_instruction(line), line


def _flat_course_m(distance_m: int) -> list[TrackPoint]:
    step = 50.0
    steps = int(distance_m / step)
    meters_per_deg = 6_371_000.0 * 3.141592653589793 / 180.0
    lat = 42.58
    points = [TrackPoint(lat=lat, lon=23.29, elevation_m=1500.0)]
    for _ in range(steps):
        lat += step / meters_per_deg
        points.append(TrackPoint(lat=lat, lon=23.29, elevation_m=1500.0))
    return points
