"""The demo entry point has to accept the plans the engine actually builds."""

from __future__ import annotations

from backend.self_check import run_self_check


def test_fuel_engine_self_check_passes() -> None:
    """`python -m backend.fuel_engine` runs this same check."""

    run_self_check()
