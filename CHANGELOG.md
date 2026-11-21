# Changelog

All notable changes to Race Fuel Co-Pilot are documented here.

## v1.0.0 — 2026-11-21
- **Trust & release readiness:** Added MIT LICENSE, Privacy Policy, Terms & Not-Medical-Advice pages linked from the app footer, in-app Changelog / What's New notice, opt-in Copy Diagnostics for bug reports, and Cloudflare deploy guide (`docs/deploy.md`).
- **12+ hour race endurance:** Added GPS quality guard (accuracy & jump filtering), adaptive polling/recalibration, OLED dark Battery Saver mode, screen wake-lock & background catch-up, and 48 px+ glove-friendly Race Mode buttons.
- **Metric / Imperial & Crew Sheet:** Added display-unit toggle (km/mi, m/ft, kg/lb, C/F) with canonical metric engine requests, plus a print- and share-friendly Crew Sheet with +/-10% ETA bands and GI rescue steps.
- **Offline-first PWA:** Full Python fueling engine runs on-device via Pyodide with stale-while-revalidate app shell and separate runtime cache.
