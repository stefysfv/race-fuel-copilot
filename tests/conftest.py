"""Shared pytest options."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register the switch that rewrites golden snapshots."""

    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Rewrite tests/golden snapshots from the current API responses.",
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    """True only when this run was started with --update-golden."""

    return bool(request.config.getoption("--update-golden"))
