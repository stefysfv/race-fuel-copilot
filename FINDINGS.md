# Invariant findings

Hypothesis checks in `tests/test_invariants.py` cover the approved plan rules. The engine was not changed. Each item below is a small input that breaks one of those rules. The property tests skip that case with a comment so the rest of the rule still runs. The `test_logged_*` tests are expected failures that keep the example executable.

## On-pace recalibration clears the next 50 minutes

Recalibrating where the runner is exactly on plan should leave the remaining stops unchanged. `generate_plan` applies the pre-race quiet window from `segments[0].start_minute`. After a snap, that start minute is the runner's clock, so the next 50 minutes are wiped even though the original plan still feeds them.

Flat 30 km, default athlete, snap at the track point nearest 60 minutes (`pace_ratio` 1). Original stops at 60 and 90 minutes are 30 g. The rebuilt plan turns both into 0 g water sips. Stops from 120 minutes onward match.

## Short-race recalibration rebuilds the suffix

Same rule, short-race protocol (`target_time_minutes=90`). Flat 20 km, snap nearest 20 minutes, on pace.

The original plan has sips at 25, 50, 75, 100, 125, and 150 minutes, and one 25 g gel at 88 minutes. The rebuilt plan has sips at 45, 70, 120, and 145 minutes, and the gel at 97 minutes. The bite is 55% of the distance still ahead, not the original bite.

## Short races ignore GI Rescue

`target_time_minutes <= 120` never reads `rescue_start_min`. Flat 12 km, `target_time_minutes=90`, `rescue_start_min=20`. The plan is the short-race sips and one gel. No stop is a rescue sip, and no reason mentions GI Rescue.

## GI Rescue ease is not kept as liquid, and the half-size feeds are spent early

Flat 30 km, default athlete, `rescue_start_min=140`. The cooldown runs until minute 185.

The 160 minute ease feed is a 12 g savoury sandwich (`real_food`), not liquid. The 180 minute ease feed is a 12 g gel. The vest menu rewrites the rescue dose. Carbs stay 12 g, which is the small ease amount, but the form does not.

An empty vest does the same pass in reverse. On the 12 km course with `vest_foods=[]` and `rescue_start_min=30`, the 50 and 60 minute ease lines still mention 12 g, then add "No gel is in the vest, and no other vest food is selected." The grams are 0. On a yellow climb the sentence is "No gel or drink mix in the vest. Sip water and salt on this climb." Same course, pace 11 min/km, `rescue_start_min=10`, the 50 minute ease feed.

The two half-size feeds are the first carb doses in the whole plan, not the two feeds after the cooldown. The 60 minute oat bar is already "Half-size while GI Rescue eases off." (15 g), eighty minutes before rescue starts. The first normal feed after minute 185, at 210 minutes, is a full 30 g.

## Packed vest overwrites a red micro-dose and the caffeine cap

All-climb 8 km (constant 15%), `weight_kg=30.1`, one packed gel: 40 g carbs, 400 mg caffeine, quantity 1.

The caffeine cap is `30.1 * 5 = 150.5` mg. The plan schedules 400 mg. The 60 minute stop is on red and is that gel (`form=gel`, 40 g). A red window with no safer ground inside ±6 minutes is supposed to be a liquid micro-dose of at most 15 g. The 90 minute stop, which the vest does not stamp, is still that 15 g micro-dose.

## Packed vest ignores the 90 minute solid gap and the savoury clock

Flat 16 km, default athlete, two packed salted chips (solid, savoury, 20 g).

The vest stamps solids at 60 minutes and 90 minutes. That is a 30 minute gap, and both are savoury, before the 150 minute switch. The default rotation spaces solids by 90 minutes and holds savoury food until that mark. The stamp runs after those rules.

## The opening quiet window erases rescue ease feeds

Flat 20 km, default athlete, `rescue_start_min=0`. The first 20 minutes are rescue sips at 0 g, which is the nil phase. The ease feeds at 20, 30, and 40 minutes should be 12 g of liquid. They are 0 g water sips whose reason is "Pre-race meal covers this window." The quiet window leaves `rescue_sip` alone and rewrites every other form. Ease feeds are liquids, so they get wiped.

## Descent lock erases a yellow rescue ease feed

Constant −10% for 20 km (start elevation 3500 m), default athlete, `rescue_start_min=35`. Every slice is yellow, grade −10%. The leg from start to finish averages −9.8%, which is below the −3.5% descent lock. The ease window is minutes 55 to 80.

The stops at 55, 65, and 75 minutes are yellow and should be 12 g of liquid. They are 0 g water sips: "Leg averages -9.8%. No trail chewing on this descent." The lock spares `rescue_sip`, and the nil sips at 35 and 45 minutes stay. Ease feeds are ordinary liquids by then, so the lock treats them as trail food. Red is the only ease exception in the rescue rule, and these stops are not red.

## A nearby plate erases a rescue ease feed

Flat 16 km, default athlete, one food station at 8 km. That pin is minute 64, and the leg from the start lasted 64 minutes, so the station serves a 28 g plate. `rescue_start_min=40`, so the ease window is minutes 60 to 85.

The 60 minute feed is 4 minutes from the plate. It should be 12 g of liquid on green. It is a 0 g sip: "Skip the vest snack. Aid Station Plate: …". The plate rule subtracts the plate from the nearest vest snack inside 15 minutes, and a remainder under 5 g becomes a sip. Twelve grams minus 28 g is that remainder. The ease feeds at 70 and 80 minutes, outside that 15 minute window, stay at 12 g. The plate itself is still eaten at the table.

## A tiny custom portion overflows packing

Flat 17 km. Athlete hourly carbs 2 g, `carb_multiplier=1.25`, `vest_foods=[]`, and one custom vest food named "a" with `carbs_g=2.2250738585072014e-308` (the smallest positive float, still inside `gt=0, le=120`).

`generate_plan` raises `OverflowError: cannot convert float infinity to integer`. The packer does `remaining // portion_carbs`. That quotient is infinity, and `int` rejects it. A portion of `1e-300` g does not crash. The property draws stay at or above one microgram so the rest of the suite can run.

## A plate plus carryover can exceed the leg budget

Flat 12 km, default athlete, food stations at 6 km and 11 km.

The leg from the first station to the second is 40 minutes and is budgeted at 46 g (`duration × 60 g/h × 1.15`). It also carries a 28 g plate at the arrival and 28 g of carryover from the plate just eaten. Vest carbs are 0. The three buckets sum to 56 g, which is more than the 46 g leg. The leg total itself still matches duration × rate × 1.15. The split does not.
