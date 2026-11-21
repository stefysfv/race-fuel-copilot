# Physiology assumptions

Every number below changes carbohydrate dose, sodium, fluid, or the clock that decides when a feed is due. The plan the athlete sees is guidance built from these numbers. It is not a measurement of that athlete's sweat, stomach, or sodium needs.

How to read a row:

- **Value** is what the code uses today. Changing a row changes plans.
- **Used in** is the file that reads it.
- **Rationale** is the plain reason the number exists.
- **Evidence** is one of `established guideline`, `heuristic`, or `author's choice`.
- **Source** cites the consensus reference for established anchors, or states `product heuristic` / `product placeholder` when no clinical guideline applies.

Five anchor rows below cite peer-reviewed sports-nutrition or biomechanics consensus papers (`established guideline`). All other constants—grade zone cutoffs, rescue timings, packing buffers, and food placeholders—are product heuristics (`heuristic` or `author's choice`), not clinical guidelines. This document has not been reviewed by a registered sports dietitian; test every fueling strategy in training.

## What the athlete is shown

The stored hourly fluid rate is unchanged. The packing card labels it as a ceiling: "up to X ml/h (drink to thirst)". The stored hourly sodium rate is unchanged. The card calls it a typical rate for this temperature and says to adjust it for sweat saltiness. A note on the packing list says that, in a long cool race, drinking the whole planned volume because it is listed can outrun thirst and dilute blood sodium.

The leg totals stored on each aid leg still include the 15% pack buffer. Those totals size flasks and salt capsules. They are not shown as an hourly drink order.

## Carbohydrate target

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `carbs_g_per_hour` | default 60; setup buttons 45, 60, 90; allowed above 0 and at most 120 | `backend/models.py`, `backend/engine/doses_menu.py`, `backend/engine/packing.py`, `frontend/index.html` | Hourly carbohydrate target (30–60 g/h for 1–2.5 h; up to 90 g/h multiple-transportable carbs for >2.5 h). | established guideline | Jeukendrup (2014), *Sports Med* 44(Suppl 1):S25–S33; Thomas, Erdman & Burke (2016), *ACSM/AND/DC Position Statement*, *Med Sci Sports Exerc* 48(3):543–568 |
| `carb_multiplier` | default 1.0; allowed 0.75 to 1.25; gut buttons send 0.88 and 1.12 | `backend/models.py`, `backend/engine/doses_menu.py` (`_scaled_athlete`), `frontend/index.html` | Scales the carb target when the athlete says their stomach runs light or heavy. The buttons are 12% either way. | author's choice | product heuristic (no clinical guideline) |
| `feed_interval_min` | default 30; allowed 15 to 45 | `backend/models.py`, `backend/engine/scheduler.py`, `backend/engine/doses_menu.py` | How often the scheduler tries to place a feed. Dose size is the hourly rate times this many minutes. | author's choice | product heuristic (no clinical guideline) |
| `WINDOW_MIN` | 6 | `backend/engine/scheduler.py` | A feed may slide up to 6 minutes onto flatter ground. | heuristic | product heuristic (no clinical guideline) |
| Yellow carb factor | 0.5, unnamed | `backend/engine/doses_menu.py` (`_dose_for_target`) | A yellow grade gets half the carbs of that feed window. | heuristic | product heuristic (no clinical guideline) |
| `RED_MICRO_DOSE_G` | 15 | `backend/engine/doses_menu.py` | A red grade is capped at 15 g, or at the window's carbs if that is smaller. Carbs that do not fit are dropped, not added to the next window. | heuristic | product heuristic (no clinical guideline) |
| `GEL_CARBS_G` | 25 | `backend/engine/packing.py` | When the vest has no named gel, a packed gel is counted as 25 g. | author's choice | product heuristic (no clinical guideline) |

## When a feed is allowed to be solid, sweet, or savoury

These change which foods are offered and therefore the carbs and sodium on that bite. They also move the clock for the first solid bite.

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `GREEN_MIN_GRADE` | −8% | `backend/engine/zones.py` | Below this, a descent is yellow until the red line. Green is the full-dose band. The README calls these lines practical heuristics, not established cutoffs. An athlete can override them. | heuristic | product heuristic (no clinical guideline) |
| `GREEN_MAX_GRADE` | +5% | `backend/engine/zones.py` | Above this, a climb is yellow until the red line. | heuristic | product heuristic (no clinical guideline) |
| `SENSITIVE_GREEN_MAX` | +3.5% | `backend/engine/zones.py` | Used only when gut sensitivity is "sensitive" and the athlete did not set their own green max. Narrows the full-dose band. | heuristic | product heuristic (no clinical guideline) |
| `YELLOW_UPHILL_MAX` | +10% | `backend/engine/zones.py` | Steeper than this, a climb is red and the dose drops to the micro-dose cap. | heuristic | product heuristic (no clinical guideline) |
| `YELLOW_DOWNHILL_MIN` | −12% | `backend/engine/zones.py` | Steeper than this, a descent is red. | heuristic | product heuristic (no clinical guideline) |
| `DESCENT_LOCK_GRADE` | −3.5% | `backend/engine/packing.py`, `backend/engine/coach_text.py` | A leg whose average grade is downhill of this gets no trail food in the pack. | heuristic | product heuristic (no clinical guideline) |
| `SOLID_CLEAR_CLIMB` | +9% | `backend/engine/doses_menu.py` | Solids are not chosen on a climb steeper than this. | heuristic | product heuristic (no clinical guideline) |
| `SOLID_CLEAR_DESCENT` | −5% | `backend/engine/doses_menu.py` | Solids are not chosen on a descent steeper than this. | heuristic | product heuristic (no clinical guideline) |
| `PRE_RACE_QUIET_MIN` | 50 | `backend/engine/coach_text.py`, `backend/engine/packing.py` | No gel or solid in the first 50 minutes. The opening pack can cover that window from the start line. | heuristic | product heuristic (no clinical guideline) |
| `OPENING_EASY_MIN` | 90 | `backend/engine/doses_menu.py`, `backend/engine/packing.py` | The first 90 minutes stay on gels and drink mix, with at most one small bar or dates. | heuristic | product heuristic (no clinical guideline) |
| `SOLID_EVERY_MIN` | 90 | `backend/engine/doses_menu.py` | After the opening, at most one solid slot per 90 minutes. | heuristic | product heuristic (no clinical guideline) |
| `SAVOURY_START_MIN` | 150 | `backend/engine/doses_menu.py`, `backend/engine/packing.py` | Savoury food is preferred from 2.5 hours. | heuristic | product heuristic (no clinical guideline) |
| `EARLY_SAVOURY_MIN` | 90 | `backend/engine/doses_menu.py` | If the athlete opts in, savoury can start at 90 minutes instead of 150. | author's choice | product heuristic (no clinical guideline) |
| `PALATE_FATIGUE_MIN` | 210 | `backend/engine/doses_menu.py`, `backend/engine/packing.py` | From 3.5 hours the menu prefers savoury. The README calls this a practical heuristic, not an established cutoff. | heuristic | product heuristic (no clinical guideline) |
| `AID_LEG_MIN_MINUTES` | 40 | `backend/engine/packing.py`, `backend/engine/scheduler.py` | An aid plate is planned only when the leg into that station lasts at least 40 minutes. | heuristic | product heuristic (no clinical guideline) |
| `PRE_RACE_SHORT_LEG_MIN` | 75 | `backend/engine/packing.py` | A short first leg uses the opening coach note. | author's choice | product heuristic (no clinical guideline) |
| `PRE_RACE_FOOD_STATION_MIN` | 100 | `backend/engine/packing.py` | A first leg of at most 100 minutes that ends at a food station also uses that opening note, and trail food is capped at one gel. | author's choice | product heuristic (no clinical guideline) |
| `CARRYOVER_WINDOW_MIN` | 55 | `backend/engine/packing.py` | On later legs, at most 55 minutes of the carb target can be treated as already covered by the previous station. | author's choice | product heuristic (no clinical guideline) |
| `SHORT_RACE_MAX_MIN` | 120 | `backend/engine/short_race.py` | A plan of at most 2 hours uses the short-race protocol instead of the hourly schedule. | author's choice | product heuristic (no clinical guideline) |
| Short-race bite clock | halfway, or 55% of the race when the target is at least 65 minutes | `backend/engine/short_race.py` | One small real-food bite. Under 65 minutes the bite is at halfway. From 65 minutes it is at 55% of the race. | author's choice | product heuristic (no clinical guideline) |
| Short-race bite fluid and sodium | 120 ml and 80 mg, unnamed | `backend/engine/short_race.py` | Fluid and sodium attached to that single bite. | author's choice | product heuristic (no clinical guideline) |
| `HYDRATION_SIP_MIN` | 25 | `backend/engine/short_race.py` | After the bite, a short race of at least 65 minutes gets a sip reminder every 25 minutes. The reminder does not name a volume. | author's choice | product heuristic (no clinical guideline) |
| `MAJOR_CLIMB_GRADE` | +8% | `backend/engine/doses_menu.py` (`_wants_caffeine`) | A climb at least this steep can pull a caffeinated gel forward. | author's choice | product heuristic (no clinical guideline) |
| Caffeine hold and lead | no caffeine before 45% of the race clock; then a window 30 to 35 minutes before a climb, both unnamed | `backend/engine/doses_menu.py` | Holds caffeine until late in the race, then aims it about half an hour before a steep climb. The constant `CLIMB_LOOKAHEAD_MIN` (20) is not the number this function reads. | author's choice | product heuristic (no clinical guideline) |

## Fluid and sodium

`sweat_rates` in `backend/engine/sweat.py` replaces `fluid_ml_per_hour` and `sodium_mg_per_hour` on every plan. A value sent in the request does not survive. Sodium in mg/h is milligrams per litre times litres per hour.

Reference body mass in the formula is 65 kg. `mass_delta` is `weight_kg - 65`.

| Band | Fluid | Sodium concentration | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| Cool, under 15°C | `450 + 3 * mass_delta` ml/h | 500 mg/L | Lower ceiling in cool air; drink to thirst to avoid exercise-associated hyponatremia. | heuristic | Fluid ceiling & EAH caution: Hew-Butler et al. (2015), *Clin J Sport Med* 25(4):303–320; Sweat [Na+] range: Baker (2017), *Sports Med* 47(Suppl 1):S111–S128; band formula is a product heuristic |
| Moderate, 15 to 25°C inclusive | `650 + 4 * mass_delta` ml/h | 700 mg/L | Middle band used for the default 18°C setup. At 70 kg this is 670 ml/h and 469 mg sodium/h. | heuristic | Baker (2017), *Sports Med* 47(Suppl 1):S111–S128 (athlete sweat [Na+] varies ~200–2000 mg/L); band formula is a product heuristic |
| Hot, over 25°C | `850 + 5 * mass_delta` ml/h | 950 mg/L | Higher ceiling in heat; still paired with drink-to-thirst guidance. | heuristic | Hew-Butler et al. (2015); Baker (2017); band formula is a product heuristic |

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `PACK_BUFFER` | 1.15 | `backend/engine/packing.py` | Leg carbs, fluid, and sodium used for packing are the hourly rate times hours times 1.15, so the pack is a bit larger than the bare product. | author's choice | product heuristic (no clinical guideline) |
| `FLASK_ML` | 500 | `backend/engine/packing.py`, `frontend/index.html` | One soft flask is treated as 500 ml when counting bottles. This is bottle size, not a sip order. | author's choice | product heuristic (no clinical guideline) |
| `FLASK_CAP` | 2 | `backend/engine/packing.py` | At most two flasks on a leg of 2.5 hours or less. | author's choice | product heuristic (no clinical guideline) |
| `FLASK_CAP_LONG` | 3 | `backend/engine/packing.py` | At most three flasks when the leg is longer than 2.5 hours. | author's choice | product heuristic (no clinical guideline) |
| `LONG_LEG_HOURS` | 2.5 | `backend/engine/packing.py` | The cut between the two flask caps. | author's choice | product heuristic (no clinical guideline) |
| `salt_cap_mg` / `CAPSULE_SODIUM_MG` | default 200 | `backend/models.py`, `backend/engine/packing.py` | Sodium counted in one salt capsule when the menu does not cover the leg. | author's choice | product heuristic (no clinical guideline) |
| Aid plate fluid | 150 ml, unnamed | `backend/engine/scheduler.py` (`_insert_aid_bites`) | Fluid attached to a planned aid-station bite. | author's choice | product heuristic (no clinical guideline) |
| Aid plate sodium fallback | 200 mg | `backend/engine/doses_menu.py` (`station_plate`) | Used when the station menu does not supply its own sodium. | author's choice | product heuristic (no clinical guideline) |
| `AID_BITE_CARBS_G` | 28 | `backend/engine/constants.py`, `backend/engine/doses_menu.py` | Default carbohydrate count for the generic aid plate. | author's choice | product heuristic (no clinical guideline) |
| Nearby-snack window | 15 minutes, unnamed | `backend/engine/scheduler.py` | A vest snack within 15 minutes of the plate is reduced by the plate's carbs. | author's choice | product heuristic (no clinical guideline) |
| Plate leftover cut | under 5 g, unnamed | `backend/engine/scheduler.py` | If the vest snack has under 5 g left after the plate, that stop becomes a sip with no carbs. | author's choice | product heuristic (no clinical guideline) |

## GI rescue

Rescue replaces the normal dose. It does not add the skipped carbs back later.

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `RESCUE_NIL_CARB_MIN` | 20 | `backend/engine/doses_menu.py` | For 20 minutes after a logged GI problem, feeds are water and salt only. | heuristic | product heuristic (no clinical guideline) |
| `RESCUE_SIP_INTERVAL_MIN` | 10 | `backend/engine/doses_menu.py` | During that 20 minutes a sip is offered every 10 minutes. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_SIP_FLUID_ML` | 80 | `backend/engine/doses_menu.py` | Fluid on each of those sips. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_SIP_SODIUM_MG` | 150 | `backend/engine/doses_menu.py` | Sodium on each of those sips. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_EASE_CARBS_G` | 12 | `backend/engine/doses_menu.py` | After the nil window, the next feeds are a small carb dose. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_EASE_FLUID_ML` | 120 | `backend/engine/doses_menu.py` | Fluid on each ease feed. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_EASE_SODIUM_MG` | 200 | `backend/engine/doses_menu.py` | Sodium on each ease feed. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_GENTLE_FEEDS` | 2 | `backend/engine/doses_menu.py` | How many ease feeds run before the normal dose returns. | author's choice | product heuristic (no clinical guideline) |
| `RESCUE_COOLDOWN_MIN` | 45 | `backend/engine/doses_menu.py` | Another GI log inside 45 minutes does not restart the whole protocol. | author's choice | product heuristic (no clinical guideline) |

## Caffeine

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `CAFFEINE_MG_PER_KG` | 5 | `backend/engine/totals_warnings.py`, `backend/models.py` | Caps planned caffeine at 5 mg/kg within the standard 3–6 mg/kg ergogenic range. | established guideline | Guest et al. (2021), *ISSN Position Stand: Caffeine and Exercise Performance*, *J Int Soc Sports Nutr* 18:1 |
| Caffeine gel in the vest catalog | 100 mg on the caffeine gel, 0 on the others | `frontend/food_menu.js` | Default caffeine the setup screen sends for that gel. The engine menu item itself has no caffeine field; the locker row carries the milligrams. | author's choice | product heuristic (no clinical guideline) |
| Locker caffeine clamp | 0 to 400 mg per item, at most 12 locker rows | `frontend/plan_request.js` | Stops a single typed row from sending an unbounded caffeine number. | author's choice | product heuristic (no clinical guideline) |

## Warnings that do not change the dose

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `DELIVERY_WARN_RATIO` | 0.70 | `backend/engine/totals_warnings.py` | A warning when delivered carbs are under 70% of the target. The schedule is not rebuilt to close the gap. | author's choice | product heuristic (no clinical guideline) |
| `DELIVERY_INFO_RATIO` | 0.85 | `backend/engine/totals_warnings.py` | A milder note between 70% and 85% of the target. | author's choice | product heuristic (no clinical guideline) |

## Default foods

These are product-size placeholders. A custom vest item uses the carb grams the athlete typed. Sodium on a custom item is not measured from a label in this repository.

Vest menu, `backend/engine/doses_menu.py`:

| Key | Carbs (g) | Sodium (mg) |
| --- | --- | --- |
| Standard gel | 25 | 50 |
| Isotonic gel | 22 | 40 |
| Drink mix | 30 | 250 |
| High-carb gel | 40 | 80 |
| Caffeine gel | 25 | 50 |
| Half sandwich | 25 | 140 |
| Oat bar | 30 | 100 |
| Medjool dates | 25 | 10 |
| Chocolate | 20 | 20 |
| Salted chips | 20 | 320 |

Aid-station menu, same file:

| Key | Carbs (g) | Sodium (mg) |
| --- | --- | --- |
| Sandwich quarters | 30 | 250 |
| Watermelon | 12 | 5 |
| Cucumber | 3 | 300 |
| Salted chips | 22 | 300 |
| Chocolate pieces | 24 | 20 |
| Apple wedges | 18 | 2 |
| Pitted dates | 28 | 5 |
| Banana pieces | 22 | 1 |
| Cola or warm broth | 18 | 200 |

Evidence for every row in both tables: author's choice. Source: product placeholder (typical retail nutrition labels; verify against your own items).

## Clock that moves the feeds

Segment minutes come from flat pace times a grade factor (`backend/engine/timing.py`, `backend/engine/gap.py`). A slower segment pushes later feeds later, so this model changes fuel timing even though it does not contain a carb or fluid number.

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| Minetti 2002 cost polynomial | `C(i) = 155.4 i^5 − 30.4 i^4 − 43.3 i^3 + 46.3 i^2 + 19.5 i + 3.6`, with `i` clamped to ±0.40 | `backend/engine/gap.py` | Relative energy cost versus flat ground. Feed times are placed on the resulting clock. | established guideline | Minetti et al. (2002), *Energy cost of walking and running at extreme uphill and downhill slopes*, *J Appl Physiol* 93(3):1039–1046; field fit in `docs/calibration-report.md` |
| `FLAT_COST_J_PER_KG_M` | 3.6 | `backend/engine/gap.py` | Divisor that turns the polynomial into a factor of 1.0 on flat ground. | established guideline | Minetti et al. (2002), *J Appl Physiol* 93(3):1039–1046 |
| `MIN_COST_J_PER_KG_M` | 1.0 | `backend/engine/gap.py` | Floor under the polynomial. On grades from −40% to +40% the polynomial stays above this floor, so the floor does not move today's plans. | author's choice | product heuristic (no clinical guideline) |

## Food choice and the race-day clock

These do not set the hourly targets. They change which food is picked, or they move the clock a live recalculation uses, so the carbs and sodium on a bite can change.

| Name | Value | Used in | Rationale | Evidence | Source |
| --- | --- | --- | --- | --- | --- |
| `FINISH_MERGE_M` | 500 m | `backend/engine/packing.py` | A station in the last 500 m is the finish, not its own leg. That changes the last leg's hours and the fluid packed for it. | author's choice | product heuristic (no clinical guideline) |
| `_ZONE_FIT_BONUS` | 1.0 | `backend/engine/packing.py` | Prefers a solid on green and a gel or drink on yellow or red when two foods would otherwise tie. | author's choice | product heuristic (no clinical guideline) |
| `_REPEAT_PENALTY` | 1.5 | `backend/engine/packing.py` | Prefers a different food from the previous bite. Larger than the zone bonus, so the vest still alternates. | author's choice | product heuristic (no clinical guideline) |
| `MIN_PACE_SAMPLE_MIN` | 5 | `backend/engine/recalibrate.py` | A live pace sample shorter than 5 minutes does not rewrite the remaining clock. | author's choice | product heuristic (no clinical guideline) |
| `MIN_PACE_RATIO` / `MAX_PACE_RATIO` | 0.6 to 1.8 | `backend/engine/recalibrate.py` | Clamps how far a live pace may scale the remaining plan. | author's choice | product heuristic (no clinical guideline) |
| `OFF_COURSE_M` | 75 m | `backend/engine/recalibrate.py` | A GPS fix farther than this from the track does not move the fuel clock. | author's choice | product heuristic (no clinical guideline) |
| `SHORT_RACE_NOTE` | "eat 2h before" | `backend/engine/constants.py` | The short-race warning tells the athlete to eat two hours before the start. | author's choice | product heuristic (no clinical guideline) |

## Defined, and not read

These names exist in `backend/engine/constants.py` and are re-exported. No scheduler, dose, pack, or sweat function reads them, so they do not change a plan today. They are listed so a reviewer does not assume they do.

| Name | Value | Evidence | Source |
| --- | --- | --- | --- |
| `CLIMB_LOOKAHEAD_MIN` | 20 | author's choice | product heuristic (no clinical guideline) |
| `SOLID_LAG_MIN` | 40 | author's choice | product heuristic (no clinical guideline) |
| `FLAT_GREEN_GRADE` | +3% | author's choice | product heuristic (no clinical guideline) |

Caffeine timing uses the unnamed 30 to 35 minute lead in `_wants_caffeine`, not `CLIMB_LOOKAHEAD_MIN`.
