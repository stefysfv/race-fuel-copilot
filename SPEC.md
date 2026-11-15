# Technical Specification: Trail & Ultra Race Fueling Co-Pilot (PWA)

## 1. Product Vision & Core Problem
Endurance trail runners frequently suffer gastrointestinal (GI) distress and bonking (hypoglycemia/dehydration) because standard watch timers alert them to eat on a blind schedule (e.g., every 30 minutes). Taking a heavy gel during a steep +18% climb when heart rate is in Zone 4/5 diverts blood away from the gut, halting digestion and causing nausea.

This app is a **terrain-aware nutrition and pacing co-pilot** built as an iOS-compatible Progressive Web App (PWA). It parses GPX race tracks, models Grade Adjusted Pace (GAP), classifies terrain into digestive safety zones, schedules exact carb/water/sodium intakes on runnable sections, provides aid-station packing lists, recalibrates live via GPS snap-to-track, and includes a one-tap "GI Rescue Mode" when a runner's stomach turns.

## 2. Project Directory Structure
```text
race-fuel-copilot/
├── .cursorrules
├── SPEC.md
├── requirements.txt
├── backend/
│   ├── main.py              # FastAPI app, CORS, static file serving, API routes
│   ├── models.py            # Pydantic schemas for requests, responses, and race data
│   ├── gpx_parser.py        # GPX XML parsing, elevation smoothing, 500m slicing, Haversine
│   ├── fuel_engine.py       # GAP pacing math, digestive zones, window-shifted fuel scheduler
│   ├── push_service.py      # Web Push notification scheduling (VAPID / pywebpush)
│   └── data/
│       ├── races.json       # Predefined Bulgarian trail races & aid stations
│       └── gpx/             # Folder for raw .gpx files
└── frontend/
    ├── index.html           # Mobile-first iOS PWA shell (Setup, Live Race, Aid Station views)
    ├── style.css            # Custom glass/outdoor high-contrast UI styles
    ├── app.js               # Main UI controller, state management, API calls
    ├── map_controller.js    # Leaflet + OpenTopoMap rendering, GPX polyline, live GPS marker
    ├── offline_db.js        # IndexedDB wrapper for caching active race plan offline
    ├── sw.js                # Service Worker for offline asset caching & Web Push listeners
    └── manifest.json        # iOS standalone PWA configuration
```
