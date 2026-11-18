"""Zip the pure engine for the browser worker.

Writes ``frontend/engine.zip`` and a copy for the spike page. The archive contains ``backend/engine``
plus the two modules that package imports and that do not touch the disk:
``backend/gpx_parser.py`` and ``backend/models.py``. Adapters, FastAPI, and
the self-check are not included.

The same command copies the catalog, the GPX files, and the golden snapshots
the spike page fetches into ``frontend/spike/fixtures/``.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "frontend" / "engine.zip"
SPIKE_ZIP = ROOT / "frontend" / "spike" / "engine.zip"
FIXTURE_DIR = ROOT / "frontend" / "spike" / "fixtures"

# Pages under backend/ that the engine imports. They stay out of backend/engine
# because the server adapters live beside them, and they do no file I/O.
ENGINE_NEIGHBORS = ("gpx_parser.py", "models.py", "__init__.py")

FIXTURES = (
    ("frontend/data/races.json", "races.json"),
    ("frontend/data/gpx/salomon-vitosha-27.gpx", "salomon-vitosha-27.gpx"),
    ("frontend/data/gpx/salomon-vitosha-40.gpx", "salomon-vitosha-40.gpx"),
    ("frontend/data/gpx/vitosha-100.gpx", "vitosha-100.gpx"),
    (
        "tests/golden/generate-plan__salomon-vitosha-40__default.json",
        "golden/generate-plan__salomon-vitosha-40__default.json",
    ),
    (
        "tests/golden/generate-plan__vitosha-100__default.json",
        "golden/generate-plan__vitosha-100__default.json",
    ),
    (
        "tests/golden/recalibrate__mid-race.json",
        "golden/recalibrate__mid-race.json",
    ),
)


def _zip_engine(destination: Path) -> None:
    """Write the engine archive. Paths inside the zip use forward slashes."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    engine_dir = ROOT / "backend" / "engine"
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in engine_dir.rglob("*"):
            if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            arcname = Path("backend") / "engine" / path.relative_to(engine_dir)
            archive.write(path, arcname.as_posix())
        for name in ENGINE_NEIGHBORS:
            archive.write(ROOT / "backend" / name, f"backend/{name}")


def _stage_fixtures() -> None:
    """Copy the files the spike page loads. The page cannot read backend/."""

    for source, target in FIXTURES:
        destination = FIXTURE_DIR / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)


def main() -> None:
    _zip_engine(ZIP_PATH)
    SPIKE_ZIP.parent.mkdir(parents=True, exist_ok=True)
    SPIKE_ZIP.write_bytes(ZIP_PATH.read_bytes())
    _stage_fixtures()
    print(f"Wrote {ZIP_PATH.relative_to(ROOT)} ({ZIP_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
