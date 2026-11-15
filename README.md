# Race Fuel Co-Pilot

Topographical and physiological fueling co-pilot for mountain ultras. It turns a GPX into a carb, fluid, and sodium plan that follows the grade, the clock, and a live GPS fix.

There is no hosted demo in this repository. Run it locally and open the PWA on a phone on the same Wi-Fi.

## The physiological problem

A fixed “one gel every 30 minutes” timer ignores four things that decide whether food stays down:

- **Steep climbs.** Above about +10%, hard effort shifts blood toward working muscle (splanchnic vasoconstriction). A dense gel in that window often comes back up.
- **Steep descents.** Below about −12%, eccentric braking sloshes a full stomach. The gel that was fine on a road is a bad idea on a loose drop.
- **Palate fatigue.** After about 3.5 hours, another sweet gel is the one runners stop taking. The long second half needs savoury food.
- **Thermal sweat drift.** Heat raises fluid and sodium loss. A plan built for 18°C under-drinks a 28°C day, and a runner who is behind the clock is not on the splits the plan assumed.

## Algorithmic architecture

**Minetti GAP solver.** Segment time is `kilometres × flat pace × C(grade) / C(0)` using the Minetti 2002 cost polynomial. Uphill costs more than flat; a mild downhill costs less. Finish time is not an engine field. Duration is linear in flat pace, so the client probes at 8 min/km and scales once: `pace = 8 × target / predicted`. The sum of segment times lands on that target (within half a minute inside the pace limits).

**Green / yellow / red zoning.** Each ~500 m slice is classified from net grade:

| Zone | Grade | Intake |
| --- | --- | --- |
| Green | −8% to +5%, endpoints included | Full carb dose |
| Yellow | above +5% through +10%, or −12% through just below −8% | Half dose, liquid |
| Red | steeper than +10% or −12% | 15 g liquid micro-dose |

A feed may slide up to 6 minutes onto safer ground. If the whole window is red, the micro-dose is taken there.

**Zero-debt micro-dosing.** A red window forfeits the rest of its carbs. That deficit is not added to the next window. Later stops stay at one dose, not more than 1.5×.

**Palate rotation.** On a race longer than 3.5 hours, green feeds at or after `max(3.5 h, halfway)` move from sweet gels to savoury food. A station with food, reached after 75 minutes, adds a plate (~28 g) and reduces the vest carry for that leg by the same amount. Water-only stations do not.

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

Python 3.12 or newer, from the repository root.

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

Open `http://127.0.0.1:8000`. `--host 0.0.0.0` lets a phone on the same network reach the app.

`tests/test_fuel_engine.py` covers the GAP target solve, zone boundaries, red-zone micro-dosing, hot-versus-cool sweat at 70 kg, savoury rotation and vest reduction at food stations, GI Rescue, and custom aid-station snapping.
