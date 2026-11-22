# Technical Specification: Trail & Ultra Race Fueling Co-Pilot (PWA)

## 1. Product Vision & Core Problem
Endurance trail runners frequently suffer gastrointestinal (GI) distress and bonking (hypoglycemia/dehydration) because standard watch timers alert them to eat on a blind schedule (e.g., every 30 minutes). Taking a heavy gel during a steep +18% climb when heart rate is in Zone 4/5 diverts blood away from the gut, halting digestion and causing nausea.

This app is a **terrain-aware nutrition and pacing co-pilot** built as an iOS-compatible Progressive Web App (PWA). It parses GPX race tracks, models Grade Adjusted Pace (GAP), classifies terrain into digestive safety zones, schedules exact carb/water/sodium intakes on runnable sections, provides aid-station packing lists, recalculates the rest of the race from a GPS check-in (snap-to-track), and includes a one-tap "GI Rescue Mode" when a runner's stomach turns.

### Check-in concept (v0.10.0)
The app is a smart **offline check-in guide** for 12+ hour mountain races, not a live coach. A phone that tracks and alerts does not survive a locked iPhone, and a screen that stays on drains the battery. So:

- **The phone stays locked** in the vest. The app sends no voice, chime, or notification cues, holds no screen wake lock, and runs no `watchPosition` or polling timer.
- **GPS runs only on demand.** Sync GPS (and GI Rescue) make one `getCurrentPosition` call inside the tap. Starting a race, building a plan, and unlocking the phone never read GPS.
- **A check-in recalculates what is left.** The fix is snapped to the course and the remaining plan is rebuilt. Live shows updated arrival times and fueling windows for the next section. A section log (Section done) sets the pace ratio from the runner's own time against plan, clamped to 0.70-1.85.
- **Finished trail and meals clear from Live.** Only what is left ahead stays on screen. Finish Run gives a recap of every intake, section by section.
- **Off course.** When the snapped distance is more than 2 km from the course, Live shows "Off course" and hides grade, zone, and expected arrival instead of planned values. A later on-course check-in restores them. (The 250 m "off-course warning" banner and the engine's 75 m on-course radius are separate and unchanged.)

The engine (`backend/engine/`) and its golden snapshots do not change for the check-in concept. Pace, arrival, and window projection for the Live screen is browser-side code in `frontend/leg_projection.js`, covered by `tests/js/`.

## 2. Project Directory Structure
```text
race-fuel-copilot/
├── .cursorrules
├── SPEC.md
├── requirements.txt
├── requirements-dev.txt
├── backend/
│   ├── main.py              # FastAPI app, CORS, static file serving, API routes
│   ├── models.py            # Pydantic schemas for requests, responses, and race data
│   ├── gpx_parser.py        # GPX XML parsing, elevation smoothing, 500m slicing, Haversine
│   ├── adapters/            # Server-only file access. The engine does not import this.
│   │   ├── catalog.py       # Reads races.json
│   │   └── gpx_files.py     # Reads a catalog GPX file into text
│   ├── fuel_engine.py       # Re-exports backend.engine so older imports keep working
│   ├── self_check.py        # Demo plan and invariant checks
│   ├── engine/              # Pure plan math: arguments in, a plan out
│   │   ├── __init__.py
│   │   ├── entry.py         # generate_plan_json / recalibrate_json
│   │   ├── constants.py
│   │   ├── gap.py
│   │   ├── zones.py
│   │   ├── sweat.py
│   │   ├── timing.py
│   │   ├── doses_menu.py
│   │   ├── short_race.py
│   │   ├── coach_text.py
│   │   ├── packing.py
│   │   ├── totals_warnings.py
│   │   ├── scheduler.py
│   │   └── recalibrate.py
│   └── data/
│       ├── races.json       # Predefined Bulgarian trail races & aid stations
│       └── gpx/             # Folder for raw .gpx files
└── frontend/
    ├── index.html           # Mobile-first iOS PWA shell (Setup, Live Race, Aid Station views)
    ├── style.css            # Custom glass/outdoor high-contrast UI styles
    ├── app.js               # Main UI controller, state management, API calls
    ├── map_controller.js    # Leaflet + OpenTopoMap rendering, GPX polyline, live GPS marker
    ├── offline_db.js        # IndexedDB wrapper for caching active race plan offline
    ├── gps.js               # Sync GPS check-in, off-course state, GI Rescue, desk simulator
    ├── gps_guard.js         # Rejects weak or jumping GPS fixes
    ├── live.js              # Live tab: next section, fueling windows, recap
    ├── leg_projection.js    # Pure pace/arrival/window math for Live (unit-tested in tests/js/)
    ├── sw.js                # Service Worker for offline asset caching
    └── manifest.json        # iOS standalone PWA configuration
```
