"""Grade Adjusted Pace cost from the Minetti 2002 running model."""

from __future__ import annotations

from backend.engine.constants import (
    FLAT_COST_J_PER_KG_M,
    MAX_ABS_GRADE_FRACTION,
    MIN_COST_J_PER_KG_M,
)

def metabolic_cost_j_per_kg_m(grade_fraction: float) -> float:
    """Energy cost of running one metre at this gradient, from Minetti 2002."""

    grade = min(MAX_ABS_GRADE_FRACTION, max(-MAX_ABS_GRADE_FRACTION, grade_fraction))
    cost = (
        155.4 * grade**5
        - 30.4 * grade**4
        - 43.3 * grade**3
        + 46.3 * grade**2
        + 19.5 * grade
        + FLAT_COST_J_PER_KG_M
    )
    return max(cost, MIN_COST_J_PER_KG_M)


def gap_cost_factor(grade_percent: float) -> float:
    """How many times longer this slope takes versus flat ground, at the same effort.

    factor = C(grade) / C(0). Uphill is above 1, mild downhill is below 1.
    """

    return metabolic_cost_j_per_kg_m(grade_percent / 100.0) / FLAT_COST_J_PER_KG_M
