# Bugs found during the engine split

These were left as they are. The split did not change plan output. `python -m pytest` passed, including `tests/test_golden.py`.

## Self-check still forbids a yellow gel

`backend/self_check.py` `_assert_plan` raises `AssertionError: A yellow segment was assigned a gel` when a yellow stop has `FuelForm.gel`.

The engine does assign a gel on yellow. On the default 27 km plan, the stop at 61.7 min is a 15 g Standard Gel on a yellow segment. That is the behavior locked by the golden snapshots.

`python -m backend.fuel_engine` therefore fails on the 27 km catalog race. The test suite does not call `run_self_check`, so pytest stays green.

## Build Plan asks the engine twice

**Fixed.** `buildPlan` calls `generatePlan` once. The request sets `match_target_time`. The engine probes the segment clock at 8 min/km, sets `pace = 8 × target / duration` (clamped to 3.51–19.9 min/km, rounded to 3 decimals), and builds the fuel plan once. A request without the flag keeps the pace it was given, so a target time on its own does not rewrite the catalog plans. Race id and uploaded GPX still share that one request.
