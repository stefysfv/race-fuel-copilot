"""Compatibility shim for the fueling engine.

The plan math lives in `backend.engine`. Names stay importable from here so
existing callers do not change. `python -m backend.fuel_engine` runs the
self-check.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __name__ == "__main__" and __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.engine.constants import (
    AID_BITE_CARBS_G,
    AID_LEG_MIN_MINUTES,
    AID_PLATE,
    CAFFEINE_MG_PER_KG,
    CAPSULE_SODIUM_MG,
    CARRYOVER_WINDOW_MIN,
    CLIMB_LOOKAHEAD_MIN,
    DEFAULT_STATION_KEYS,
    DESCENT_LOCK_GRADE,
    EARLY_SAVOURY_MIN,
    FINISH_MERGE_M,
    FLASK_CAP,
    FLASK_CAP_LONG,
    FLASK_ML,
    FLAT_COST_J_PER_KG_M,
    FLAT_GREEN_GRADE,
    FRUIT_STATION_KEYS,
    GAP_MODEL_NAME,
    GEL_CARBS_G,
    GEL_VEST_KEYS,
    GREEN_MAX_GRADE,
    GREEN_MIN_GRADE,
    HEAVY_VEST_KEYS,
    HYDRATION_SIP_MIN,
    LONG_LEG_HOURS,
    MAJOR_CLIMB_GRADE,
    MAX_ABS_GRADE_FRACTION,
    MAX_PACE_RATIO,
    METERS_PER_DEG_LAT,
    MIN_COST_J_PER_KG_M,
    MIN_PACE_RATIO,
    MIN_PACE_SAMPLE_MIN,
    OFF_COURSE_M,
    OPENING_EASY_MIN,
    PACK_BUFFER,
    PALATE_FATIGUE_MIN,
    PRE_RACE_FOOD_STATION_MIN,
    PRE_RACE_QUIET_MIN,
    PRE_RACE_SHORT_LEG_MIN,
    RED_MICRO_DOSE_G,
    RESCUE_COOLDOWN_MIN,
    RESCUE_EASE_CARBS_G,
    RESCUE_EASE_FLUID_ML,
    RESCUE_EASE_SODIUM_MG,
    RESCUE_GENTLE_FEEDS,
    RESCUE_NIL_CARB_MIN,
    RESCUE_SIP_FLUID_ML,
    RESCUE_SIP_INTERVAL_MIN,
    RESCUE_SIP_SODIUM_MG,
    SALT_STATION_KEYS,
    SAVOURY_START_MIN,
    SAVOURY_VEST_KEYS,
    SENSITIVE_GREEN_MAX,
    SHORT_RACE_MAX_MIN,
    SHORT_RACE_NOTE,
    SHORT_RACE_SIP,
    SOLID_CLEAR_CLIMB,
    SOLID_CLEAR_DESCENT,
    SOLID_EVERY_MIN,
    SOLID_LAG_MIN,
    WINDOW_MIN,
    YELLOW_DOWNHILL_MIN,
    YELLOW_UPHILL_MAX,
    _REPEAT_PENALTY,
    _ZONE_FIT_BONUS,
)

from backend.engine.gap import (
    gap_cost_factor,
    metabolic_cost_j_per_kg_m,
)

from backend.engine.zones import (
    _green_max_for,
    _zone_rank,
    classify_zone,
)

from backend.engine.sweat import (
    _with_sweat_rates,
    sweat_rates,
)

from backend.engine.timing import (
    _approach_minutes,
    _average_grade,
    _distance_at_minute,
    _minute_at_distance,
    _place_feed,
    _segment_index_at,
    _stop_on_clock,
    _truncate_front,
    build_timed_segments,
)

from backend.engine.doses_menu import (
    STATION_MENU,
    VEST_MENU,
    _Dose,
    _MenuClock,
    _MenuItem,
    _Target,
    _align_reason_with_intake,
    _apply_food_preference,
    _apply_locker,
    _apply_named_food,
    _climb_without_gel,
    _dose_for_target,
    _gels_selected,
    _green_reason,
    _kind_for_custom,
    _menu_items,
    _overlay_menu_item,
    _pick_item,
    _replace_gel_claim,
    _replace_liquid_half_dose,
    _resolved_foods,
    _savoury_minute,
    _scaled_athlete,
    _wants_caffeine,
    _yellow_reason,
    station_plate,
)

from backend.engine.short_race import (
    _ShortBite,
    _is_short_race,
    _limit_short_race,
    _short_race_bite,
    _short_race_stops,
)

from backend.engine.coach_text import (
    _coach_note,
    _departure_has_food,
    _descent_coach_note,
    _descent_stop_reason,
    _quiet_descent_stops,
    _quiet_opening_stops,
    _quiet_trail_stop,
)

from backend.engine.packing import (
    _VestStock,
    _choose_packed,
    _consume_packed_vest,
    _eat_detail,
    _flask_item,
    _inventory_summary,
    _is_vest_slot,
    _keep_pack_line,
    _leg_index_for_km,
    _pack_for_preference,
    _pack_items,
    _pack_named_portions,
    _pack_plain,
    _packed_form,
    _solid_choice,
    _stamp_packed,
    _zone_fit_bonus,
    build_aid_legs,
)

from backend.engine.totals_warnings import (
    _active_fuel_minutes,
    _carbs_delivered_on_leg,
    _delivered_grams_per_hour,
    _delivery_warnings,
    _distance_warnings,
    _effective_carb_target,
    _leg_minutes_after_quiet,
    _stations_on_course,
    _totals,
    _zone_counts,
)

from backend.engine.scheduler import (
    _feed_targets,
    _insert_aid_bites,
    generate_plan,
    generate_plan_for_race,
    schedule_fuel,
)

from backend.engine.recalibrate import (
    _pace_ratio,
    _recalibrate_message,
    _to_local_xy,
    recalibrate,
    snap_custom_aids,
    snap_to_track,
)

from backend.self_check import (
    _assert_plan,
    _check_invariants,
    _demo_segment,
    _format_hours,
    _format_pack_item,
    _print_plan,
    _vest_carbs_packed,
    run_self_check,
)

from backend.adapters.catalog import (
    get_race,
    load_race_catalog,
)

if __name__ == "__main__":
    run_self_check()
