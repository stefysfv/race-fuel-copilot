# Race Fuel Co-Pilot

Topographical and physiological fueling co-pilot for mountain ultras. It turns a GPX into a carb, fluid, and sodium plan that follows the grade, the clock, and a live GPS fix.

There is no hosted demo in this repository. Local setup below is for a computer at home. Race day uses the Render service described with `render.yaml`.

## Not medical advice

Carb, fluid, and sodium figures are estimates and defaults, so test them in training and adjust. This is not medical advice.

## The physiological problem

A fixed “one gel every 30 minutes” timer ignores four things that decide whether food stays down:

- **Steep climbs.** Above about +10%, hard effort shifts blood toward working muscle (splanchnic vasoconstriction). A dense gel in that window often comes back up.
- **Steep descents.** Below about −12%, eccentric braking sloshes a full stomach. The gel that was fine on a road is a bad idea on a loose drop.
- **Palate fatigue.** A practical heuristic, not a measured cutoff: after a few hours another sweet gel is the one runners stop taking, so the long second half needs savoury food. The engine makes that switch at 2.5 h, or at 1.5 h when early savoury is on.
- **Thermal sweat drift.** Heat raises fluid and sodium loss. A plan built for 18°C under-drinks a 28°C day, and a runner who is behind the clock is not on the splits the plan assumed.

## Algorithmic architecture

**Minetti GAP solver.** Segment time is `kilometres × flat pace × C(grade) / C(0)` using the Minetti 2002 cost polynomial. Uphill costs more than flat; a mild downhill costs less. Minetti is a running-cost model. Very steep climbs are usually power-hiked, so predicted time on them is approximate. Finish time is not an engine field. Duration is linear in flat pace, so the client probes at 8 min/km and scales once: `pace = 8 × target / predicted`. The sum of segment times lands on that target (within half a minute inside the pace limits).

**Green / yellow / red zoning.** Each ~500 m slice is classified from net grade. These lines are practical heuristics for when a full gel is a poor bet, not established physiological cutoffs.

| Zone | Grade | Intake |
| --- | --- | --- |
| Green | −8% to +5%, endpoints included | Full carb dose |
| Yellow | above +5% through +10%, or −12% through just below −8% | Half dose (gel or liquid/drink mix) |
| Red | steeper than +10% or −12% | 15 g liquid micro-dose |

A feed may slide up to 6 minutes onto safer ground. If the whole window is red, the micro-dose is taken there.

**Zero-debt micro-dosing.** A red window forfeits the rest of its carbs. That deficit is not added to the next window. Later stops stay at one dose, not more than 1.5×.

**Palate rotation.** Green feeds move to a savoury solid at 150 minutes (2.5 h), or at 90 minutes (1.5 h) when `early_savoury` is on. Those marks are `SAVOURY_START_MIN` and `EARLY_SAVOURY_MIN`. `PALATE_FATIGUE_MIN` is 210 minutes (3.5 h). On a race longer than that, `_savoury_minute` also computes `max(3.5 h, halfway)` and keeps the earlier of that value and the 2.5 h or 1.5 h mark, so the switch stays at 2.5 h, or at 1.5 h with early savoury. The 3.5 h figure is a planning heuristic, not a physiological cutoff. Before that mark, a green solid is a bar or dates rather than a sandwich, and a solid is due at most every 90 minutes. The first 90 minutes stay on gels and drink mix, with at most one small bar or dates after the 50-minute quiet window. Solids wait when the grade is steeper than +9% or below −5%. Yellow stays on a half dose of gel or drink mix. A station with food whose arriving leg lasted at least 40 minutes adds a plate (about 28 g on the default menu) and reduces the vest carry for that leg by the same amount. Water-only stations do not.

**Live GPS and GI Rescue.** `POST /api/recalibrate` snaps the fix to the track and sets `pace_ratio = elapsed / predicted` there (clamped). The remaining plan is rebuilt from that point. `gi_rescue: true` and `gi_distress: true` are the same switch:

1. **20 minutes** of water-and-salt sips and 0 g of carbohydrate.
2. Until **45 minutes**, small liquid carbs and no solid food.
3. The **next two** feeds are half size, then the plan returns to a full dose.

Custom GPX uploads can include aid stations (`name`, `distance_km`, `has_water`, `has_food`, `cutoff_hours`). Each kilometer is snapped to the nearest trackpoint for latitude, longitude, and elevation. The Setup drawer and a map-tap mode both feed that list.

## Tech stack and PWA

- **API.** Python 3.12+, FastAPI, Pydantic v2. Sports math stays in `backend/fuel_engine.py` and `backend/gpx_parser.py`.
- **UI.** Vanilla HTML, CSS, and ES modules. Leaflet draws the route on OpenTopoMap. Chart.js draws elevation and zone color.
- **Offline.** IndexedDB stores the active plan. The service worker is network-first for the app shell. Map tiles that were already viewed stay in the browser cache.
- **Hands-free race mode.** `watchPosition` recalibrates about every 45 seconds and auto-logs an intake 30 seconds after `00:00` (with undo). Alerts play a double chime, speak the instruction with the Web Speech API, and post a service-worker notification for the lock screen: 2 minutes out, at `00:00`, and inside 500 m of an aid station. iOS delivers those after the app is on the Home Screen.

## Local setup and tests

Python 3.12 or newer, from the repository root. A phone on the same Wi-Fi can open the app only while that computer is at home. On race day the phone is on the mountain, so the backend has to be reachable there.

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
.\.venv\Scripts\python.exe -m pytest
```

macOS or Linux:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m pytest
```

Open `http://127.0.0.1:8000` on the computer. `--host 0.0.0.0` lets a phone on the same home Wi-Fi use that computer's address. That path stops working once the phone leaves the house.

`render.yaml` is a Render Blueprint for a free Python web service. The build command is `pip install -r requirements.txt`. The start command is `uvicorn backend.main:app --host 0.0.0.0 --port $PORT`, with Python 3.12.8. Connect the repo in Render and apply that Blueprint, then open the service URL on the phone and add the app to the Home Screen.

The free plan sleeps after it sits idle, and the next request waits while the service cold-starts. Open the app and generate the plan before the start, while you still have signal.

The page and the API share one host, so the server allows no cross-origin browser calls unless `ALLOWED_ORIGINS` is set. That variable is a comma-separated list of origins, read when the process starts. Leave it unset for local dev and for the Render service. Set it only when a different site must call the API, for example `https://crew.example.com,http://127.0.0.1:8000`.

## What works offline

Once a plan has been generated and the map has been opened, the phone can keep going without the server:

- The saved plan
- The packing list
- Map marker snapping onto the saved course
- Map tiles that were already viewed

Recalibration and plan regeneration need the server. With no signal, a GPS fix still moves the marker on the saved course and leaves the fuel plan as it was.

`tests/test_fuel_engine.py` covers the GAP target solve, zone boundaries, red-zone micro-dosing, hot-versus-cool sweat at 70 kg, savoury rotation and vest reduction at food stations, GI Rescue, and custom aid-station snapping.

## Code map

`backend/fuel_engine.py` is one module. The sections below are the functions to open first.

- **Zones, GAP, sweat** — `classify_zone`, `gap_cost_factor`, `sweat_rates`, `build_timed_segments` (around lines 367–494).
- **Short races** — `_short_race_bite`, `_short_race_stops`, `_limit_short_race` (around 496–733).
- **Plan entry** — `generate_plan`, `schedule_fuel` (around 989–1212).
- **Coach text** — `_coach_note`, `_quiet_opening_stops`, `_quiet_descent_stops` (around 1214–1342).
- **Packing** — `build_aid_legs`, `_pack_items` (around 1344–1470 and 2413–2595).
- **Live recalibration** — `snap_to_track`, `recalibrate` (around 1500–1607).
- **Doses and menu** — `_dose_for_target`, `_apply_food_preference`, `_savoury_minute` (around 1693–1767 and 1909–2064).
- **Delivery report** — `_delivered_grams_per_hour`, `_delivery_warnings`, `_totals` (around 2597–2715).

`frontend/app.js` is the page controller.

- **Build and storage** — `readAthlete`, `buildPlan`, `wireCriticalControls`, `resetOutdatedStorage` (around 218–467).
- **Packing screen** — `renderPacking`, `packCard`, `deliveryRateLabel` (around 516–643).
- **Map and tabs** — `drawMap`, `showTab` (around 814–860).
- **Race mode and GPS** — `onRaceModeClick`, `armGpsWatch`, `onRaceGps`, `sendFix` (around 941–1052 and 1574–1631).
- **Voice and alerts** — `coachSpeech`, `announce`, `pushNote` (around 1179–1343).
- **Vest and stations** — `setFuelPreference`, `renderVestRows`, `toggleFood` (around 2067–2494).
- **Startup** — `bind`, `boot` (around 2689–2911).
