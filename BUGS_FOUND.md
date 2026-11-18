# Bugs found during the engine split

These were left as they are. The split did not change plan output. `python -m pytest` passed, including `tests/test_golden.py`.

## Self-check still forbids a yellow gel

`backend/self_check.py` `_assert_plan` raises `AssertionError: A yellow segment was assigned a gel` when a yellow stop has `FuelForm.gel`.

The engine does assign a gel on yellow. On the default 27 km plan, the stop at 61.7 min is a 15 g Standard Gel on a yellow segment. That is the behavior locked by the golden snapshots.

`python -m backend.fuel_engine` therefore fails on the 27 km catalog race. The test suite does not call `run_self_check`, so pytest stays green.
