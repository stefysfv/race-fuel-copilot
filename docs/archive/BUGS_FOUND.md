# Bugs found during the engine split

Both items below are fixed. Plan output was not changed to close them.

## Self-check still forbids a yellow gel

**Fixed.** `_assert_plan` no longer rejects a gel on yellow. A yellow trail stop that is not a station plate and not a GI Rescue sip must stay within half the target window and must not be solid food. A half dose of gel or drink mix still passes. `tests/test_self_check.py` calls `run_self_check()`.

## Build Plan asks the engine twice

**Fixed.** `buildPlan` calls `generatePlan` once. The request sets `match_target_time`. The engine probes the segment clock at 8 min/km, sets `pace = 8 × target / duration` (clamped to 3.51–19.9 min/km, rounded to 3 decimals), and builds the fuel plan once. A request without the flag keeps the pace it was given, so a target time on its own does not rewrite the catalog plans. Race id and uploaded GPX still share that one request.
