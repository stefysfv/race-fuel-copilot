# Changelog

All notable changes to Race Fuel Co-Pilot are documented here.

## v0.10.0-beta — 2026-11-22
- **Check-in concept:** The app is now a smart offline check-in guide for 12+ hour mountain races. The phone stays locked in your vest. At an aid station, or when you slow down, tap **Sync GPS** (about 10 seconds) and the rest of the race is recalculated: arrival times and fueling windows. Finished sections and meals clear from Live, and Finish Run lists everything you ate.
- **GPS only on tap:** Location is read with one `getCurrentPosition` call inside the Sync GPS or GI Rescue tap. Starting a race, building a plan and unlocking the phone no longer read GPS.
- **Off course:** More than 2 km from the course, Live shows "Off course" and hides grade, zone, and expected arrival instead of planned values.
- **Removed:** Screen wake lock, `watchPosition`, adaptive polling, and the empty voice/chime/notification stubs (`frontend/alerts.js`, the service-worker notification handler). Battery diagnostics now report check-ins instead of polling, wake lock, and speech.
- **Fixed:** GI Rescue referenced an undefined `accuracy`, so it always fell back to the clock position instead of using the GPS fix. Text and number inputs now turn off autocomplete, autocorrect, autocapitalize, and spellcheck.
- **Tests:** Added Node unit tests (`npm run test:js`, also in CI) for the Live projection: pace from the log, ratio clamping, restored sections, and no aid stations. The projection moved to the DOM-free `frontend/leg_projection.js`. Collapsed an identical-branch ternary in `projectLegs` (no behavior change). Engine and golden snapshots are unchanged.

## v0.9.0-beta — 2026-11-21
- **Trust & release readiness:** Added MIT LICENSE, Privacy Policy, Terms & Not-Medical-Advice pages linked from the app footer, in-app Changelog / What's New notice, opt-in Copy Diagnostics for bug reports, and Cloudflare deploy guide (`docs/deploy.md`).
- **Long-ultra battery features (beta — field battery test pending):** Added GPS quality guard (accuracy & jump filtering), adaptive polling/recalibration, OLED dark Battery Saver mode, screen wake-lock & background catch-up, and 48 px+ glove-friendly Race Mode buttons.
- **Metric / Imperial & Crew Sheet:** Added display-unit toggle (km/mi, m/ft, kg/lb, C/F) with canonical metric engine requests, plus a print- and share-friendly Crew Sheet with +/-10% ETA bands and GI rescue steps.
- **Offline-first PWA:** Full Python fueling engine runs on-device via Pyodide with stale-while-revalidate app shell and separate runtime cache.
