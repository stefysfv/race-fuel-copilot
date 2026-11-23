"""Shared fueling numbers and food-key sets. Nothing here reads a file or builds a plan."""

from __future__ import annotations

# Minetti et al., J Appl Physiol, 2002. Gradient is rise/run, not percent.
# C(i) = 155.4 i^5 - 30.4 i^4 - 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6  (J/kg/m)
FLAT_COST_J_PER_KG_M = 3.6
MIN_COST_J_PER_KG_M = 1.0
MAX_ABS_GRADE_FRACTION = 0.40

# Green is closed on both ends. Yellow uphill starts just above +5% and
# includes +10%. Yellow downhill starts just below -8% and includes -12%.
GREEN_MIN_GRADE = -8.0
GREEN_MAX_GRADE = 5.0
YELLOW_UPHILL_MAX = 10.0
YELLOW_DOWNHILL_MIN = -12.0
WINDOW_MIN = 6.0
PACK_BUFFER = 1.15
GEL_CARBS_G = 25.0
FLASK_ML = 500.0
CAPSULE_SODIUM_MG = 200.0
# Steep ground cannot take a full gel. 15 g is the most this window will feed.
RED_MICRO_DOSE_G = 15.0
# A caffeinated gel is placed before a steep climb, or once the clock is
# past halfway, and the running total stops at 5 mg per kg of body weight.
CAFFEINE_MG_PER_KG = 5.0
CAFFEINE_GAP_MIN = 90.0
CLIMB_LOOKAHEAD_MIN = 20.0
MAJOR_CLIMB_GRADE = 8.0
# After 3.5 hours, green feeds in the second half leave the sweet gels behind.
PALATE_FATIGUE_MIN = 210.0
SAVOURY_START_MIN = 150.0
EARLY_SAVOURY_MIN = 90.0
# The first 90 minutes stay on gels and drink mix. A station plate is only
# worth sitting down for when the leg that arrives there lasted 40 minutes.
OPENING_EASY_MIN = 90.0
SOLID_EVERY_MIN = 90.0
AID_LEG_MIN_MINUTES = 40.0
# A race of two hours or less still has glycogen. One bottle and one small bite.
SHORT_RACE_MAX_MIN = 120.0
SHORT_RACE_NOTE = (
    "Short Race Protocol: Rely on pre-race carb-loading (eat 2h before). "
    "Minimal mid-race fueling required."
)
GEL_VEST_KEYS = frozenset({"gel_25", "iso_gel_22", "high_carb_gel", "caffeine_gel"})
# The pre-race meal covers the opening. Trail gels and solids wait.
PRE_RACE_QUIET_MIN = 50.0
# Delivered rate after that window, as a fraction of the stored hourly target.
# Under 70% stays the plan warning. From 70% up to 85% is an info note on
# FuelBudget. At 85% and above the rate line is quiet. Short races skip both.
DELIVERY_WARN_RATIO = 0.70
DELIVERY_INFO_RATIO = 0.85
PRE_RACE_SHORT_LEG_MIN = 75.0
PRE_RACE_FOOD_STATION_MIN = 100.0
CARRYOVER_WINDOW_MIN = 55.0
DESCENT_LOCK_GRADE = -3.5
SOLID_LAG_MIN = 40.0
SOLID_CLEAR_CLIMB = 9.0
SOLID_CLEAR_DESCENT = -5.0
FLASK_CAP = 2
FLASK_CAP_LONG = 3
LONG_LEG_HOURS = 2.5
# A pin in the last 500 m is the finish, not a one-minute extra leg.
FINISH_MERGE_M = 500.0
SENSITIVE_GREEN_MAX = 3.5
AID_BITE_CARBS_G = 28.0
FLAT_GREEN_GRADE = 3.0
AID_PLATE = "Aid Station Plate: 2 pieces banana / salted potatoes / orange wedges + broth/cola"

# GI Rescue: 20 min with no calories, then small liquid carbs until 45 min,
# then two half-size feeds. Each of those feeds stands alone.
RESCUE_NIL_CARB_MIN = 20.0
RESCUE_COOLDOWN_MIN = 45.0
RESCUE_SIP_INTERVAL_MIN = 10.0
RESCUE_SIP_FLUID_ML = 80.0
RESCUE_SIP_SODIUM_MG = 150.0
RESCUE_EASE_CARBS_G = 12.0
RESCUE_EASE_FLUID_ML = 120.0
RESCUE_EASE_SODIUM_MG = 200.0
RESCUE_GENTLE_FEEDS = 2

# Below a few minutes of evidence, a GPS fix near the start must not rewrite the race.
MIN_PACE_SAMPLE_MIN = 5.0
MIN_PACE_RATIO = 0.6
MAX_PACE_RATIO = 1.8
OFF_COURSE_M = 75.0
GAP_MODEL_NAME = "minetti_2002"

METERS_PER_DEG_LAT = 111_320.0

DEFAULT_STATION_KEYS = ("sandwich", "watermelon", "cucumber", "chips", "cola_broth")
SAVOURY_VEST_KEYS = frozenset({"sandwich", "chips"})
HEAVY_VEST_KEYS = frozenset({"sandwich", "chocolate", "chips"})
FRUIT_STATION_KEYS = frozenset({"watermelon", "apple", "banana"})
SALT_STATION_KEYS = frozenset({"cucumber", "chips"})

HYDRATION_SIP_MIN = 25.0
SHORT_RACE_SIP = "Hydration check · Take 3–4 sips from flask"

# A solid on green, or a gel/liquid on yellow and red, gets this added to
# its pick score. 1.0 is enough to break a tie, and smaller than the 1.5
# repeat penalty, so the vest still alternates.
_ZONE_FIT_BONUS = 1.0
_REPEAT_PENALTY = 1.5
