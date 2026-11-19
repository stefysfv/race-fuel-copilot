# Bugs found during the engine split

These were left as they are. The split did not change plan output. `python -m pytest` passed, including `tests/test_golden.py`.

## Self-check still forbids a yellow gel

`backend/self_check.py` `_assert_plan` raises `AssertionError: A yellow segment was assigned a gel` when a yellow stop has `FuelForm.gel`.

The engine does assign a gel on yellow. On the default 27 km plan, the stop at 61.7 min is a 15 g Standard Gel on a yellow segment. That is the behavior locked by the golden snapshots.

`python -m backend.fuel_engine` therefore fails on the 27 km catalog race. The test suite does not call `run_self_check`, so pytest stays green.

## Build Plan asks the engine twice

`frontend/build_plan.js` `buildPlan` calls `generatePlan` once at a flat 8 min/km, then calls `generatePlan` again with a pace scaled so the predicted finish matches the hours and minutes on the form.

`docs/device-test.md` says Build Plan makes two calls, and that the diagnostics "last plan time" is only the most recent one. The first call is still made. Left unchanged.
