# Race Fuel Co-Pilot

Terrain-aware fueling for trail and ultra races. The plan follows the grade under your feet, not a flat “one gel every 30 minutes” timer.

## Problem

A fixed gel timer fails in the mountains for four mechanical reasons:

- **Steep climbs.** Above about +10% grade, hard effort pulls blood toward working muscle (splanchnic vasoconstriction). A dense gel in that window sits in the gut and often comes back up.
- **Steep descents.** Below about −12%, eccentric braking sloshes a full stomach. The same gel that was fine on a forest road is a bad idea on a loose drop.
- **Palate fatigue.** After about 3.5 hours, another sweet gel is the thing runners stop taking. The second half of a long race needs savoury food, not a fourth flavor of syrup.
- **Weather and pace drift.** Heat raises sweat and sodium. A runner who is 15 minutes behind plan is not on the clock the plan was built for.

The co-pilot rebuilds the schedule from the GPX, the air temperature, and a live GPS fix.

## Core algorithm

**Minetti GAP.** Segment time is `kilometres × flat pace × C(grade) / C(0)`, using the Minetti 2002 cost polynomial. Uphill costs more than flat; a mild downhill costs less. A target finish is not an engine input. Duration is linear in flat pace, so the app probes at 8 min/km and scales once: `pace = 8 × target / predicted`. The sum of segment times lands on that target.

**Digestive zones.** Each ~500 m slice is classified from its net grade:

| Zone | Grade | What to take |
| --- | --- | --- |
| Green | −8% to +5% | Full carb dose. Gels or real food. |
| Yellow | +5% to +10%, or −12% to −8% | Half dose, as liquid. |
| Red | steeper than +10% or −12% | 15 g liquid micro-dose. The rest of that window is dropped. |

Green includes the endpoints (−8% and +5%). Yellow includes +10% and −12%. A feed can slide up to 6 minutes to reach safer ground. If the whole window is red, the micro-dose is scheduled there.

**Zero carb debt.** A skipped red window does not inflate the next green one. Each window is `hourly target × interval` on its own, then cut by the zone. Later stops stay at one dose, not 1.5× or more.

**Palate rotation.** On a race longer than 3.5 hours, green feeds at or after `max(3.5 h, halfway)` switch from sweet gels to savoury food (rice cake, pretzels, potato, and similar). A food aid station reached after 75 minutes adds a plate (about 28 g) and lightens the vest snack next to it. Water-only stations do not.

**Live GPS and GI Rescue.** `POST /api/recalibrate` snaps the fix to the track and sets `pace_ratio = elapsed / predicted` at that point (clamped). The remaining plan is rebuilt from there. `gi_rescue: true` starts the stomach protocol at the current race clock:

1. **20 minutes** of water-and-salt sips, 0 g of carbohydrate.
2. Until **45 minutes**, 12 g liquid carbs when the grade allows, still no solid food.
3. The **next two** normal feeds are half size, then the plan returns to a full dose.

## Architecture

Mobile-first PWA. No frontend framework.

- **API.** Python 3.12+, FastAPI, Pydantic v2. Sports math stays in `backend/fuel_engine.py` and `backend/gpx_parser.py`. `POST /api/generate-plan` and `POST /api/recalibrate` are the plan contract. Sample courses are the Vitosha GPX files under `backend/data/`.
- **UI.** Vanilla HTML, CSS, and ES modules. Leaflet draws the route on OpenTopoMap. Chart.js draws the elevation and zone profile.
- **Offline.** IndexedDB keeps the active plan. The service worker is network-first for the app shell, so a reload picks up new code and a dead network still opens the last shell. Map tiles that were already viewed stay in the browser cache.
- **Hands-free race mode.** On the Live tab, Race Mode calls `watchPosition`, recalibrates about every 45 seconds, and auto-logs an intake 30 seconds after `00:00` (with undo). Race alerts play a double chime, speak the instruction through the Web Speech API, and post a service-worker notification for the lock screen (2 minutes out, at `00:00`, and inside 500 m of an aid station). iOS shows those notifications after the app is on the Home Screen.

## Quickstart

From the repository root, with Python 3.12 or newer:

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

macOS or Linux:

```bash
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000`. `--host 0.0.0.0` is what lets a phone on the same Wi-Fi reach the app.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

```bash
.venv/bin/python -m pytest
```

`tests/test_fuel_engine.py` covers the GAP target-time solve, zone boundaries, red-zone micro-dosing with no carb debt, hot-versus-cool sweat at 70 kg, savoury rotation plus aid-station plates, and the GI Rescue API.
