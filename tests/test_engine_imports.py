"""The engine package must import with only the standard library and pydantic."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = """
import importlib
import pkgutil
import sys


class _BlockServerPackages:
    def find_spec(self, fullname, path, target=None):
        root = fullname.split(".", 1)[0]
        if root in {"fastapi", "gpxpy", "uvicorn"}:
            raise ImportError(root + " is blocked")
        return None


sys.meta_path.insert(0, _BlockServerPackages())
import backend.engine

for module in pkgutil.walk_packages(backend.engine.__path__, backend.engine.__name__ + "."):
    importlib.import_module(module.name)
print("engine-import-ok")
"""


def test_engine_imports_without_fastapi_gpxpy_or_uvicorn() -> None:
    completed = subprocess.run(
        [sys.executable, "-c", _SCRIPT],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "engine-import-ok" in completed.stdout
