"""Ensure server pydantic pin matches the bundled Pyodide wheel."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS_PATH = ROOT / "frontend" / "vendor" / "pyodide" / "VERSIONS.json"
REQUIREMENTS_PATH = ROOT / "requirements.txt"


def test_requirements_pydantic_matches_pyodide_versions() -> None:
    versions = json.loads(VERSIONS_PATH.read_text(encoding="utf-8"))
    expected = versions["packages"]["pydantic"]
    req_text = REQUIREMENTS_PATH.read_text(encoding="utf-8")
    match = re.search(r"^pydantic==([0-9A-Za-z._-]+)\s*$", req_text, flags=re.MULTILINE)
    assert match is not None, "requirements.txt must pin pydantic==<version>"
    assert match.group(1) == expected, (
        f"requirements.txt pins pydantic=={match.group(1)}, "
        f"but {VERSIONS_PATH.name} ships pydantic=={expected}"
    )
