"""Read a GPX file for a catalog race. Parsing the text is the engine's job."""

from __future__ import annotations

from pathlib import Path

from backend.gpx_parser import parse_gpx_xml
from backend.models import Race, TrackPoint

# Catalog paths are site-relative, like `data/gpx/salomon-vitosha-27.gpx`.
_SITE_ROOT = Path(__file__).resolve().parents[2] / "frontend"


def resolve_gpx_path(gpx_file: str) -> Path:
    """Resolve a catalog path like `data/gpx/salomon-vitosha-27.gpx`."""

    path = Path(gpx_file)
    if not path.is_absolute():
        path = _SITE_ROOT / path
    return path


def read_gpx_text(gpx_file: str) -> str:
    """Return the GPX document stored at a catalog path."""

    path = resolve_gpx_path(gpx_file)
    if not path.is_file():
        raise ValueError(f"GPX file not found: {gpx_file}")
    return path.read_text(encoding="utf-8")


def load_gpx_file(gpx_file: str) -> list[TrackPoint]:
    """Read every track point from a GPX file on disk."""

    return parse_gpx_xml(read_gpx_text(gpx_file))


def attach_track(race: Race) -> Race:
    """Return the race with `profile` filled from its GPX file.

    Catalog races store a path instead of thousands of coordinates. Slicing
    still happens later, in `prepare_course` and `slice_segments`.
    """

    if len(race.profile) >= 2 or not race.gpx_file:
        return race
    return race.model_copy(update={"profile": load_gpx_file(race.gpx_file)})
