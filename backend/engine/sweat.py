"""Hourly fluid and sodium from air temperature and body weight."""

from __future__ import annotations

from backend.models import AthleteInput

def sweat_rates(temperature_c: float, weight_kg: float) -> tuple[float, float]:
    """Hourly fluid (ml) and sodium (mg) from air temperature and body weight.

    Cool (<15°C):       450 + 3 * (kg - 65) ml/h, 500 mg sodium per litre.
    Moderate (15–25°C): 650 + 4 * (kg - 65) ml/h, 700 mg/L.
    Hot (>25°C):        850 + 5 * (kg - 65) ml/h, 950 mg/L.
    """

    mass_delta = weight_kg - 65.0
    if temperature_c < 15.0:
        ml_per_hour = 450.0 + 3.0 * mass_delta
        mg_per_liter = 500.0
    elif temperature_c <= 25.0:
        ml_per_hour = 650.0 + 4.0 * mass_delta
        mg_per_liter = 700.0
    else:
        ml_per_hour = 850.0 + 5.0 * mass_delta
        mg_per_liter = 950.0
    return ml_per_hour, mg_per_liter * ml_per_hour / 1000.0


def _with_sweat_rates(athlete: AthleteInput) -> AthleteInput:
    """Copy the athlete with fluid and sodium set by the temperature formula."""

    ml_per_hour, sodium_per_hour = sweat_rates(athlete.temperature_c, athlete.weight_kg)
    return athlete.model_copy(
        update={
            "fluid_ml_per_hour": ml_per_hour,
            "sodium_mg_per_hour": sodium_per_hour,
        }
    )
