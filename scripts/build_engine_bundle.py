"""Zip the pure engine for the browser worker and sync its cache-busting tag.

Writes ``frontend/engine.zip`` and updates the ``?v=<hash8>`` tag in
``frontend/runtime_manifest.js``, ``frontend/engine.worker.js``, and
``frontend/engine_client.js`` so phones never keep executing a stale engine.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "frontend" / "engine.zip"

ENGINE_NEIGHBORS = ("__init__.py", "gpx_parser.py", "models.py")
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)
_VERSION_FILES = (
    "frontend/runtime_manifest.js",
    "frontend/engine.worker.js",
    "frontend/engine_client.js",
)


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def engine_members(root: Path | None = None) -> list[tuple[str, bytes]]:
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
    target = destination or ZIP_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in engine_members(root):
            info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data, compresslevel=9)
    return target


def engine_version_tag(zip_path: Path | None = None) -> str:
    data = (zip_path or ZIP_PATH).read_bytes()
    return hashlib.sha256(data).hexdigest()[:8]


def sync_engine_version(tag: str, root: Path | None = None) -> None:
    base = root or ROOT
    pattern = re.compile(r"(engine\.(?:zip|worker\.js)\?v=)[0-9A-Za-z._-]+")
    for rel in _VERSION_FILES:
        path = base / rel
        text = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")
        updated = pattern.sub(rf"\g<1>{tag}", text)
        path.write_bytes(updated.encode("utf-8"))


def main() -> None:
    path = write_engine_zip()
    tag = engine_version_tag(path)
    sync_engine_version(tag)
    print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size} bytes, ?v={tag})")


if __name__ == "__main__":
    main()
