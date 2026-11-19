# Race Fuel Co-Pilot

Topographical and physiological fueling co-pilot for mountain ultras. It turns a GPX into a carb, fluid, and sodium plan that follows the grade, the clock, and a live GPS fix.

There is no hosted demo in this repository. Race day is a static site. FastAPI is only the local dev server and the test harness.

## Not medical advice

Carb, fluid, and sodium figures are estimates and defaults, so test them in training and adjust. This is not medical advice.

## Privacy

Plans and recalibrations are computed on the phone. The GPX, the athlete profile, and the fueling plan stay in the browser. Nothing is uploaded. There are no analytics and no accounts.

Notes from "How did it go?" are saved in `localStorage` on that phone. They leave the device only if you press **Send feedback**. That action opens a mail draft or a feedback form you chose to fill in. The draft contains the race name, finish versus predicted time, GI trouble, what you changed, and the note. It does not contain a GPS track.

## The physiological problem

A fixed “one gel every 30 minutes” timer ignores four things that decide whether food stays down:

- **Steep climbs.** Above about +10%, hard effort shifts blood toward working muscle (splanchnic vasoconstriction). A dense gel in that window often comes back up.
- **Steep descents.** Below about −12%, eccentric braking sloshes a full stomach. The gel that was fine on a road is a bad idea on a loose drop.
- **Palate fatigue.** A practical heuristic, not a measured cutoff: after a few hours another sweet gel is the one runners stop taking, so the long second half needs savoury food. The engine makes that switch at 2.5 h, or at 1.5 h when early savoury is on.
- **Thermal sweat drift.** Heat raises fluid and sodium loss. A plan built for 18°C under-drinks a 28°C day, and a runner who is behind the clock is not on the splits the plan assumed.

## Algorithmic architecture

**Minetti GAP solver.** Segment time is `kilometres × flat pace × C(grade) / C(0)` using the Minetti 2002 cost polynomial. Uphill costs more than flat; a mild downhill costs less. Minetti is a running-cost model. Very steep climbs are usually power-hiked, so predicted time on them is approximate. Finish time is not an engine field. Duration is linear in flat pace. Build Plan sends one request. The engine probes the segment clock at 8 min/km and builds the fuel plan once at `pace = 8 × target / predicted`. The sum of segment times lands on that target (within half a minute inside the pace limits).

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

- **Engine.** Python 3.12+, Pydantic v2. Sports math stays in `backend/engine/`. `backend/fuel_engine.py` re-exports it. GPX parsing stays in `backend/gpx_parser.py`. The phone runs that package in Pyodide. FastAPI is the dev server.
- **UI.** Vanilla HTML, CSS, and ES modules. Leaflet draws the route on OpenTopoMap. Chart.js draws elevation and zone color.
- **Offline.** IndexedDB stores the active plan. The app shell is one cache, stale-while-revalidate: a cached file is shown immediately and refreshed in the background. Pyodide and `engine.zip` are a second cache, cache-first, and a shell update does not delete it. Map tiles that were already viewed stay in the browser cache.
- **Hands-free race mode.** `watchPosition` recalibrates about every 45 seconds and auto-logs an intake 30 seconds after `00:00` (with undo). Alerts play a double chime, speak the instruction with the Web Speech API, and post a service-worker notification for the lock screen: 2 minutes out, at `00:00`, and inside 500 m of an aid station. iOS delivers those after the app is on the Home Screen.

## Local setup and tests

Python 3.12 or newer, from the repository root. Uvicorn serves the static app and the optional HTTP engine. A phone on the same Wi-Fi can open that computer only while it is at home.

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
.\.venv\Scripts\python.exe -m pytest
```

macOS or Linux:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
.venv/bin/python -m pytest
```

Open `http://127.0.0.1:8000` on the computer. `--host 0.0.0.0` lets a phone on the same home Wi-Fi use that computer's address. That path stops working once the phone leaves the house.

The page builds and recalibrates in the browser. `ENGINE_MODE` in `frontend/engine_client.js` is `"worker"`. Set it to `"http"`, or open `?engine=http`, to post plans to this FastAPI process instead. That flag is for local debugging.

Build `frontend/engine.zip` before the first worker load:

```powershell
.\.venv\Scripts\python.exe scripts\build_engine_bundle.py
```

## Static hosting

Publish the `frontend/` directory on Cloudflare Pages or Netlify. There is no build step on the host: `engine.zip` and `vendor/pyodide/` are already in that folder. Leave FastAPI and `render.yaml` off the race-day site. They stay in the repo for tests and for `?engine=http`.

After the first visit the banner reads **Ready for offline**. Add the site to the Home Screen. iOS can delete storage for a site it treats as unused, and `navigator.storage.persist()` is the request to keep it.

## What works offline

After **Ready for offline**, the phone can do all of this with no signal:

- Generate a plan for a stored race or an uploaded GPX
- Recalibrate from a GPS fix, including GI Rescue
- The saved plan and the packing list
- Map marker snapping onto the saved course
- Map tiles that were already viewed

OpenTopoMap tiles and the Inter font still need a network the first time they are requested. A shell update does not clear the Pyodide cache.

`tests/offline_race.spec.mjs` loads the app, waits for that ready state, goes offline, builds a plan, and recalibrates. `tests/e2e/smoke.spec.mjs` is the safety net for UI refactors. It selects the bundled 12 km race, builds a plan, and checks the summary, the elevation chart, the fuel-stop markers on that chart, and a packing card. It then builds again with the network off, recalibrates from a mocked GPS fix, and uploads `frontend/data/gpx/salomon-vitosha-12.gpx` with Playwright's file input. One screenshot of each main view (setup, packing, map, live) is written to `tests/e2e/screenshots/` for a side-by-side look.

These Playwright files are dev-only. The race-day site is the `frontend/` directory. `SHELL_FILES` in `frontend/runtime_manifest.js` does not list `tests/`, and the service worker never precaches them.

With uvicorn already on port 8000:

```powershell
npm install
npx playwright install chromium
npx playwright test tests/offline_race.spec.mjs
npx playwright test tests/e2e
```

`tests/test_fuel_engine.py` covers the GAP target solve, zone boundaries, red-zone micro-dosing, hot-versus-cool sweat at 70 kg, savoury rotation and vest reduction at food stations, GI Rescue, and custom aid-station snapping.


## Code map

`backend/fuel_engine.py` re-exports the engine. Open the module that owns the behavior.

- **Constants** — `backend/engine/constants.py`.
- **GAP** — `metabolic_cost_j_per_kg_m`, `gap_cost_factor` in `backend/engine/gap.py`.
- **Zones** — `classify_zone`, `_green_max_for` in `backend/engine/zones.py`.
- **Sweat** — `sweat_rates` in `backend/engine/sweat.py`.
- **Timing** — `build_timed_segments`, `_place_feed`, `_stop_on_clock` in `backend/engine/timing.py`.
- **Doses and menu** — `_dose_for_target`, `_apply_food_preference`, `_savoury_minute` in `backend/engine/doses_menu.py`.
- **Short races** — `_short_race_bite`, `_short_race_stops`, `_limit_short_race` in `backend/engine/short_race.py`.
- **Coach text** — `_coach_note`, `_quiet_opening_stops`, `_quiet_descent_stops` in `backend/engine/coach_text.py`.
- **Packing** — `build_aid_legs`, `_pack_items` in `backend/engine/packing.py`.
- **Delivery report** — `_delivered_grams_per_hour`, `_delivery_warnings`, `_totals` in `backend/engine/totals_warnings.py`.
- **Plan entry** — `generate_plan`, `schedule_fuel` in `backend/engine/scheduler.py`.
- **Live recalibration** — `snap_to_track`, `recalibrate` in `backend/engine/recalibrate.py`.
- **Race files** — `frontend/data/races.json` and `frontend/data/gpx/`. The dev server reads those same files from `backend/adapters/catalog.py`. GPX text is parsed in `backend/gpx_parser.py`. The engine package does not read files.
- **Browser entry** — `generate_plan_json` and `recalibrate_json` in `backend/engine/entry.py`. The page calls them through `frontend/engine_client.js`.

`frontend/app.js` boots the page and wires the controls. The rest of the UI is native ES modules beside it.

- **State** — `state`, `ui`, presets, and default targets in `frontend/state.js`.
- **Helpers** — `frontend/ui_helpers.js`.
- **Offline checklist** — `frontend/offline_status.js`.
- **Install card** — `frontend/install_prompt.js`.
- **Post-race feedback** — `frontend/feedback.js`. `FEEDBACK_EMAIL` and `FEEDBACK_FORM_URL` at the top of that file are the only places to point Send feedback.
- **Saved session** — `persist` and `restore` in `frontend/session.js`.
- **Vest, stations, and gut** — `frontend/food_menu.js`.
- **Packing cards** — `frontend/packing.js`.
- **Place on the plan** — `frontend/plan_cursor.js`.
- **Live tab** — `frontend/live.js`.
- **Map** — `frontend/map_screen.js`. Leaflet stays in `frontend/map_controller.js`. The elevation chart is `frontend/elevation_chart.js`.
- **Plan request** — `frontend/plan_request.js`.
- **Build Plan** — `frontend/build_plan.js`.
- **Voice and alerts** — `frontend/alerts.js`.
- **GPS and the desk simulator** — `frontend/gps.js`.
- **Tabs and setup panes** — `frontend/navigation.js`.
- **Engine transport** — `frontend/engine_client.js`. Offline snap math is `frontend/offline_engine.js`. Saved sessions sit in `frontend/offline_db.js`.


## Battery field test (60-minute outdoor walk)

Verify real battery drain before race day so you know your phone will clear 12+ hours.

1. Charge to at least 80% and open the installed app with `?diag=1` (e.g. `https://your-site/?diag=1`), or check diagnostics before and after.
2. Build a plan for any 12-40 km course, open the **Live** tab, enable **Race Mode** (and **Battery Saver** for a 12+ hour race), and tap **I ate** once to start the clock.
3. Record start time and exact battery `%` (plus `Work per hour` from `?diag=1`).
4. Walk outdoors for **60 minutes** with GPS active:
   - **iPhone (Safari / Home Screen PWA):** Keep the app in foreground with Low Power Mode off first (or on if you race with it on), screen at ~40-50% brightness, and lock the phone for two 5-minute stretches to confirm background catch-up on unlock. Note iOS battery `%` at 0 min and 60 min (Settings > Battery also shows Safari/Web App share).
   - **Android (Chrome / Installed PWA):** Set Location to High Accuracy, disable battery optimization for Chrome/PWA if needed, keep screen at ~40-50% brightness, and lock/unlock twice during the hour. Note Android battery `%` at 0 min and 60 min (Settings > Battery > Battery usage).
5. At 60 minutes, open `?diag=1`, tap **Copy as JSON**, and record:
   - Battery drain `% / hour` (target **<= 6-7% / hour** with Battery Saver so a full charge clears 12+ hours with margin).
   - `GPS fix interval`, `Recalibrations`, `Wake lock`, and `Work per hour`.
