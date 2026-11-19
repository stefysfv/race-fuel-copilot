# Invariant findings

Hypothesis checks in `tests/test_invariants.py` cover the approved plan rules. Each open item below is a small input that breaks one of those rules. The property tests skip that case with a comment so the rest of the rule still runs. The open `test_logged_*` tests are expected failures that keep the example executable.

All eleven items are fixed. The property tests in `tests/test_invariants.py` run with no expected failures.

## On-pace recalibration clears the next 50 minutes

**Fixed.** The quiet window is minute 0 to 50 on the race clock. A snap does not open a new 50 minute gap at the runner's elapsed time.

Recalibrating where the runner is exactly on plan leaves the remaining stops unchanged. `generate_plan` used to apply the pre-race quiet window from `segments[0].start_minute`. After a snap, that start minute was the runner's clock, so the next 50 minutes were wiped even though the original plan still feeds them.

Flat 30 km, default athlete, snap at the track point nearest 60 minutes (`pace_ratio` 1). Original stops at 60 and 90 minutes are 30 g, and the rebuilt plan keeps those 30 g stops. Stops from 120 minutes onward match as well.

## Short-race recalibration rebuilds the suffix

**Fixed.** An on-pace remainder keeps the original short-race stops that are still ahead. The bite and the sip grid are placed on the full race clock, then stops behind the snap are dropped.

Same rule, short-race protocol (`target_time_minutes=90`). Flat 20 km, snap nearest 20 minutes, on pace.

The original plan has sips at 25, 50, 75, 100, 125, and 150 minutes, and one 25 g gel at 88 minutes. Those stops stay when they are still ahead, with the same minute, distance, carbs, form, and zone. The rebuilt plan used to move the sips to 45, 70, 120, and 145 minutes and the gel to 97 minutes, because the bite was 55% of the distance still ahead.

## Short races ignore GI Rescue

**Fixed.** A short race with GI Rescue on uses the rescue protocol. Nil sips are 0 g for 20 minutes, ease sips are 12 g of liquid for the next 25 minutes on ground that is not red, and the next two normal feeds are half-size.

`target_time_minutes <= 120` used to ignore `rescue_start_min`. Flat 12 km, `target_time_minutes=90`, `rescue_start_min=20` was only the short-race sips and one gel. That plan now includes rescue sips.

## GI Rescue ease is not kept as liquid, and the half-size feeds are spent early

**Fixed.** An ease sip stays 12 g of liquid. The vest menu, including an empty vest, does not rename it or zero it. The two half-size feeds are the first normal doses after the 45 minute window.

Flat 30 km, default athlete, `rescue_start_min=140`. The cooldown runs until minute 185.

The 160 and 180 minute ease feeds used to be a 12 g sandwich and a 12 g gel. They stay 12 g liquids. An empty vest used to append "No gel is in the vest" and set the grams to 0. Those sips stay 12 g.

The 60 minute oat bar used to be "Half-size while GI Rescue eases off." (15 g), eighty minutes before rescue starts. That feed is full size. The first normal feed after minute 185 is the half-size feed.

## Packed vest overwrites a red micro-dose and the caffeine cap

**Fixed.** A packed piece on red is still a micro-dose of at most 15 g. Caffeine from the packed vest and from auto-assigned gels stops at 5 mg/kg.

All-climb 8 km (constant 15%), `weight_kg=30.1`, one packed gel: 40 g carbs, 400 mg caffeine, quantity 1.

The caffeine cap is `30.1 * 5 = 150.5` mg. The 60 minute stop is on red. It stays a 15 g micro-dose, and the plan does not schedule the 400 mg. The 90 minute stop, which the vest does not stamp, is still that 15 g micro-dose.

## Packed vest ignores the 90 minute solid gap and the savoury clock

**Fixed.** A packed solid is not placed within 90 minutes of another solid, and a savoury solid waits until the savoury clock.

Flat 16 km finishes near minute 128. After the quiet window, two trail bites cannot sit 90 minutes apart, so the two savoury chips stay in the vest instead of landing at minute 60 and minute 90. On a longer flat race the chips that do fit are 90 minutes apart and not before minute 150.

## The opening quiet window erases rescue ease feeds

**Fixed.** The quiet window still clears ordinary trail food in the first 50 minutes. A `GI_RESCUE_EASE` sip is exempt, the same way a nil sip and a station plate are.

Flat 20 km, default athlete, `rescue_start_min=0`. The first 20 minutes are rescue sips at 0 g. The ease feeds at 20, 30, and 40 minutes stay 12 g of liquid. They used to become 0 g water sips, "Pre-race meal covers this window."

## Descent lock erases a yellow rescue ease feed

**Fixed.** Descent lock still clears ordinary trail food when the leg averages below −3.5%. It does not clear a non-red ease sip. Red ground is still the only case that zeros an ease sip.

Constant −10% for 20 km (start elevation 3500 m), default athlete, `rescue_start_min=35`. The ease window is minutes 55 to 80. The stops at 55, 65, and 75 minutes are yellow and stay 12 g of liquid. They used to be 0 g water sips: "Leg averages -9.8%. No trail chewing on this descent."

## A nearby plate erases a rescue ease feed

**Fixed.** A plate still reduces the nearest ordinary vest snack inside 15 minutes. It does not subtract from a `GI_RESCUE_EASE` sip.

Flat 16 km, default athlete, one food station at 8 km (minute 64). `rescue_start_min=40`, so minute 60 is an ease feed, 4 minutes from the plate. That sip stays 12 g of liquid on green. It used to become a 0 g sip: "Skip the vest snack. Aid Station Plate: …". The plate itself is still eaten at the table.

## A tiny custom portion overflows packing

**Fixed.** A portion count whose division is not a finite number of pieces is 0. `generate_plan` returns a plan.

Flat 17 km. Athlete hourly carbs 2 g, `carb_multiplier=1.25`, `vest_foods=[]`, and one custom vest food named "a" with `carbs_g=2.2250738585072014e-308` (the smallest positive float, still inside `gt=0, le=120`).

The packer used to do `remaining // portion_carbs`. That quotient is infinity, and `int` rejected it with `OverflowError`. The same portion now contributes no counted pieces, and the plan still has its segments.

## A plate plus carryover can exceed the leg budget

**Fixed.** Vest carbs, the station plate, and carryover add up to the leg budget. When both plates are larger than the leg, the plate at the table stays and the carryover shrinks.

Flat 12 km, default athlete, food stations at 6 km and 11 km.

The leg from the first station to the second is 40 minutes and is budgeted at 46 g (`duration × 60 g/h × 1.15`). It used to carry a 28 g plate at the arrival and 28 g of carryover from the plate just eaten, with vest carbs at 0. Those three buckets were 56 g. Carryover is now the room left after the 28 g plate, and the three buckets match the 46 g leg. The leg total is still duration × rate × 1.15.
