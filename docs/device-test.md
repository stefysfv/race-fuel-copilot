# Phone test protocol

Use this on a real phone before race day. Open the site with `?diag=1` so the diagnostics panel is on screen. The panel has **Copy as JSON**. Paste that JSON into the notes column after each check.

Worker ready time is the wait from idle startup until the engine answers. **cold** means a Pyodide file was missing from the cache when startup began. **cached** means every Pyodide file was already stored. Last plan time is the most recent engine call. Build Plan makes one call. Copy the JSON after the packing list appears. Last recalibrate time is the most recent recalibration only.

The Offline tab is a checklist. It should read `offline ready: yes` before you turn the radio off. The Home Screen line is a hint. It does not decide offline ready.

## 1. Cold start after force-close

1. On Wi-Fi, open the site with `?diag=1`.
2. Leave Configure on screen. Confirm the race picker, weight field, and map tab all respond before the header says `Ready for offline`.
3. Open the Offline tab. Wait until it says `offline ready: yes` and the diagnostics panel shows a worker ready time.
4. Copy the JSON. This first visit is the cold start.
5. Force-close the browser. On iPhone, swipe the browser away from the app switcher. On Android, close the browser from the recent-apps list.
6. Reopen the same `?diag=1` URL. Wait for `Ready for offline`. Copy the JSON. This row is the cached start.

## 2. Airplane mode: plan and recalibrate

1. Start from `offline ready: yes` and a copied cached JSON.
2. Turn on airplane mode. Leave Wi-Fi and mobile data off.
3. On Configure, choose the 27 km race and tap Build Plan.
4. Confirm a packing list appears, including Cherni Vrah.
5. Open Live and tap GI Rescue.
6. Confirm the live message is filled in and does not say the plan was not rebuilt.
7. Copy the JSON.

## 3. Low-battery mode

1. Turn airplane mode off and reconnect to Wi-Fi.
2. Turn on Low Power Mode (iPhone) or Battery Saver (Android).
3. Force-close the browser, then reopen `?diag=1`.
4. Wait for `Ready for offline`. Build the 27 km plan again.
5. Open Live and tap GI Rescue once.
6. Copy the JSON. Write the power-saving mode name in the notes.

## 4. Ten recalibrations

1. Stay on Live with a plan already built. Keep `?diag=1` open.
2. Tap GI Rescue ten times, waiting until the live message updates before the next tap.
3. After each tap, write the panel's last recalibrate time into the table.
4. After the tenth tap, copy the JSON once.

## Results

| Check | Phone | Cold or cached | Worker ready (ms) | Plan (ms) | Recalibrate (ms) | Offline ready | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| First open |  | cold |  |  |  |  |  |
| Force-close reopen |  | cached |  |  |  |  |  |
| Airplane plan + recalibrate |  |  |  |  |  |  |  |
| Low-battery plan + recalibrate |  |  |  |  |  |  |  |
| Recalibration 1 |  |  |  |  |  |  |  |
| Recalibration 2 |  |  |  |  |  |  |  |
| Recalibration 3 |  |  |  |  |  |  |  |
| Recalibration 4 |  |  |  |  |  |  |  |
| Recalibration 5 |  |  |  |  |  |  |  |
| Recalibration 6 |  |  |  |  |  |  |  |
| Recalibration 7 |  |  |  |  |  |  |  |
| Recalibration 8 |  |  |  |  |  |  |  |
| Recalibration 9 |  |  |  |  |  |  |  |
| Recalibration 10 |  |  |  |  |  |  |  |

## Recorded baseline — 2026-11-19

Shell cache: `race-fuel-v23`.

Device: iPhone, iOS 18.7, Safari 26.6.1.

User agent: `Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6.1 Mobile/15E148 Safari/604.1`

| Run | Worker ready (ms) | Plan `lastPlanMs` | Recalibrate `lastRecalibrateMs` | Notes |
| --- | --- | --- | --- | --- |
| Standard | 1,791 (cached) | 30 | 30 | |
| Heavy | 1,810 (cached) | 87 | 153 | Low Power Mode. Plan and recalibrate are the 100 km race. |

Offline caches: `offlineReady: true`, 0 missing. Shell 45/45, races 5/5, Pyodide 10/10, engine 2/2.

Storage: 22.98 MB (~21.9 MiB) used of 41.23 GB quota. `persisted: false` on a normal Safari tab. Add to Home Screen is recommended so WebKit does not evict the site after 7 days.
