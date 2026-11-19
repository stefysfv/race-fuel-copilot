"""Structured reasons for stops and forfeited carbs.

The English sentence on a stop stays as it is. These codes are an extra
record of the branch that moved the stop or changed its grams. Params are
numbers only, so the frontend can translate them later.
"""

from __future__ import annotations

from backend.models import ForfeitedDose, ReasonCode, StopReason

# A smaller gap is float noise on a segment boundary, not a move the runner sees.
SHIFT_EPSILON_MIN = 0.05


def make_reason(code: ReasonCode, **params: float) -> StopReason:
    """One reason. Every param is stored as a float."""

    return StopReason(
        code=code,
        params={key: float(value) for key, value in params.items()},
    )


def dropped(
    at_min: float,
    carbs_g: float,
    code: ReasonCode,
    **params: float,
) -> ForfeitedDose | None:
    """A forfeited row, or None when there is nothing positive to record."""

    if carbs_g <= 0:
        return None
    return ForfeitedDose(
        at_min=at_min,
        carbs_g=float(carbs_g),
        code=code,
        params={key: float(value) for key, value in params.items()},
    )


def shift_reasons(
    ideal_minute: float,
    scheduled_minute: float,
    grade_percent: float,
    segment_index: int,
) -> list[StopReason]:
    """SHIFTED_EARLIER or SHIFTED_LATER when the clock move is real."""

    delta = scheduled_minute - ideal_minute
    if abs(delta) <= SHIFT_EPSILON_MIN:
        return []
    code = ReasonCode.SHIFTED_LATER if delta > 0 else ReasonCode.SHIFTED_EARLIER
    return [
        make_reason(
            code,
            minutes=delta,
            grade_percent=grade_percent,
            segment_index=segment_index,
        )
    ]
