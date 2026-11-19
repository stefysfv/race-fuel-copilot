"""Zip the pure engine for the browser worker.

Writes ``frontend/engine.zip``. The archive contains ``backend/engine`` plus
the modules that package imports and that do not touch the disk:
``backend/__init__.py``, ``backend/gpx_parser.py``, and ``backend/models.py``.
Adapters, FastAPI, and the self-check are not included.

Member order is sorted, text is stored with LF newlines, and zip metadata is
fixed so two builds of the same sources match.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "frontend" / "engine.zip"

# Pages under backend/ that the engine imports. They stay out of backend/engine
# because the server adapters live beside them, and they do no file I/O.
ENGINE_NEIGHBORS = ("__init__.py", "gpx_parser.py", "models.py")

# ZIP epoch. A moving timestamp would change the archive when the code did not.
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _lf(data: bytes) -> bytes:
    """Store text with LF newlines. The engine archive is Python source only."""

    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def engine_members(root: Path | None = None) -> list[tuple[str, bytes]]:
    """Archive paths and LF-normalized bytes, in the order written to the zip."""

    base = root or ROOT
    engine_dir = base / "backend" / "engine"
    paths = [
        path
        for path in engine_dir.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
    ]
    members: list[tuple[str, bytes]] = []
    for path in sorted(paths, key=lambda item: item.relative_to(engine_dir).as_posix()):
        name = "backend/engine/" + path.relative_to(engine_dir).as_posix()
        members.append((name, _lf(path.read_bytes())))
    for name in ENGINE_NEIGHBORS:
        path = base / "backend" / name
        members.append((f"backend/{name}", _lf(path.read_bytes())))
    return members


def write_engine_zip(destination: Path | None = None, root: Path | None = None) -> Path:
    """Write the deterministic engine archive and return its path."""

    target = destination or ZIP_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in engine_members(root):
            info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data, compresslevel=9)
    return target


def main() -> None:
    path = write_engine_zip()
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
