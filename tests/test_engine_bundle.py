"""The committed engine zip must match the Python sources byte for byte.

The zip is rebuilt in memory from ``backend/engine`` plus the modules that
package imports. Text is compared after LF newline normalization, so a
Windows checkout does not look like a different engine.

Keep the member order aligned with ``scripts/build_engine_bundle.py``.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ZIP_PATH = ROOT / "frontend" / "engine.zip"
ENGINE_NEIGHBORS = ("__init__.py", "gpx_parser.py", "models.py")
_ZIP_DATE = (1980, 1, 1, 0, 0, 0)


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _members() -> list[tuple[str, bytes]]:
    engine_dir = ROOT / "backend" / "engine"
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
        path = ROOT / "backend" / name
        members.append((f"backend/{name}", _lf(path.read_bytes())))
    return members


def _zip_bytes(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(filename=name, date_time=_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, data, compresslevel=9)
    return buffer.getvalue()


def test_committed_engine_zip_matches_sources() -> None:
    """Member names and LF-normalized contents match frontend/engine.zip."""

    members = _members()
    expected_names = [name for name, _data in members]
    assert ZIP_PATH.is_file(), "frontend/engine.zip is missing. Run scripts/build_engine_bundle.py."

    rebuilt = zipfile.ZipFile(io.BytesIO(_zip_bytes(members)))
    with zipfile.ZipFile(ZIP_PATH) as committed:
        assert committed.namelist() == expected_names, (
            "frontend/engine.zip members do not match backend/engine. "
            "Run scripts/build_engine_bundle.py and commit the zip. "
            f"Missing {sorted(set(expected_names) - set(committed.namelist()))}. "
            f"Extra {sorted(set(committed.namelist()) - set(expected_names))}."
        )
        assert rebuilt.namelist() == committed.namelist()
        for name, data in members:
            assert _lf(committed.read(name)) == data, (
                f"{name} in frontend/engine.zip does not match the source. "
                "Run scripts/build_engine_bundle.py and commit the zip."
            )
            assert _lf(rebuilt.read(name)) == data

    assert "backend/self_check.py" not in expected_names
    assert "backend/main.py" not in expected_names
    assert "backend/gpx_parser.py" in expected_names
    assert "backend/models.py" in expected_names
    engine_dir = ROOT / "backend" / "engine"
    for path in engine_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        arc = "backend/engine/" + path.relative_to(engine_dir).as_posix()
        assert arc in expected_names
