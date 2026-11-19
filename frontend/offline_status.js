/** Offline checklist and the ?diag=1 panel. */

import { diagnosticsSnapshot, offlineSetupFacts, onEngineStatus } from "./engine_client.js";
import { isInstalled } from "./install_prompt.js";
import {
  DAY_MS,
  FIRST_OFFLINE_KEY,
  OFFLINE_VERIFIED_KEY,
  readJson,
  readText,
  writeJson,
  writeText,
} from "./local_store.js";
import { batteryDiagSnapshot } from "./battery_diag.js";
import { state } from "./state.js";

const STALE_MS = 14 * DAY_MS;
const EVICT_NOTE = "The browser may evict cached files after inactivity. Install the app to the Home Screen and open it every few days.";

export function cacheLine(group) {
  if (!group) return "—";
  if (!group.missing.length) return `${group.cached}/${group.total} cached`;
  return `${group.cached}/${group.total} cached; missing ${group.missing.join(", ")}`;
}

export function paintDiag(report) {
  const panel = document.querySelector("#diag-panel");
  if (!panel || panel.hidden) return;
  const box = document.querySelector("#diag-json");
  const first = readJson(FIRST_OFFLINE_KEY);
  const battery = batteryDiagSnapshot();
  if (box) {
    box.value = JSON.stringify({
      ...report,
      installed: isInstalled(),
      lastVerifiedOffline: verifiedAt(),
      firstOffline: first,
      battery,
    }, null, 2);
  }
  const list = document.querySelector("#diag-list");
  if (!list) return;
  const readyText = report.workerReadyMs == null
    ? "not yet"
    : `${report.workerReadyMs} ms (${report.workerStart || "unknown"})`;
  const rows = [
    ["Worker ready", readyText],
    ["Last plan", report.lastPlanMs == null ? "—" : `${report.lastPlanMs} ms`],
    ["Last recalibrate", report.lastRecalibrateMs == null ? "—" : `${report.lastRecalibrateMs} ms`],
    ["Shell cache", cacheLine(report.caches.shell)],
    ["Races cache", cacheLine(report.caches.races)],
    ["Pyodide cache", cacheLine(report.caches.pyodide)],
    ["Engine cache", cacheLine(report.caches.engine)],
    ["Installed to Home Screen", isInstalled() ? "Yes" : "No"],
    ["Storage persisted", String(report.storage.persisted)],
    ["Storage estimate", `${report.storage.usage ?? "?"} of ${report.storage.quota ?? "?"}`],
    ["Last verified offline", formatStamp(verifiedAt())],
    ["First offline setup", first?.readyMs == null ? "not yet" : `${first.readyMs} ms`],
    ["Bytes cached", first?.bytes == null ? "not yet" : String(first.bytes)],
    ["Cache retry", first ? (first.retry ? "yes" : "no") : "not yet"],
    ["Offline ready", report.offlineReady ? "yes" : `no — ${report.missing.join(", ")}`],
    ["GPS fix interval", battery.medianFixIntervalMs == null ? "none yet" : `${(battery.medianFixIntervalMs / 1000).toFixed(1)} s median (${battery.gpsFixes} ok, ${battery.gpsRejectedAccuracy} weak, ${battery.gpsRejectedJump} jumps)`],
    ["Recalibrations", String(battery.recalibrations)],
    ["Speech / notifications", `${battery.speechCount} speech / ${battery.notificationCount} notifications`],
    ["Wake lock", battery.wakeLockState],
    ["Polling / Battery saver", `${battery.pollingState} (${battery.pollingMode}) · saver ${battery.batterySaver ? "on" : "off"}`],
    ["Work per hour", battery.workSummary],
    ["User agent", report.userAgent],
  ];
  list.replaceChildren();
  for (const [label, value] of rows) {
    const item = document.createElement("div");
    const name = document.createElement("dt");
    name.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    item.append(name, detail);
    list.append(item);
  }
}

function verifiedAt() {
  const at = Number(readText(OFFLINE_VERIFIED_KEY));
  return Number.isFinite(at) ? at : null;
}

function formatStamp(ms) {
  if (!ms) return "not yet";
  const date = new Date(ms);
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function paintStaleBanner() {
  const banner = document.querySelector("#offline-stale");
  if (!banner) return;
  const at = verifiedAt();
  const stale = at != null && Date.now() - at > STALE_MS;
  banner.classList.toggle("hidden", !stale);
  banner.textContent = stale ? "Open this app on Wi-Fi to confirm it still works offline." : "";
}

async function cachedByteTotal() {
  if (!("caches" in window)) return null;
  let total = 0;
  const names = await caches.keys();
  for (const name of names) {
    const cache = await caches.open(name);
    for (const request of await cache.keys()) {
      const response = await cache.match(request);
      const length = Number(response?.headers.get("content-length"));
      if (Number.isFinite(length) && length > 0) total += length;
    }
  }
  return total;
}

async function rememberOffline(report) {
  paintStaleBanner();
  if (!report.offlineReady) return;
  writeText(OFFLINE_VERIFIED_KEY, String(Date.now()));
  paintStaleBanner();
  if (readJson(FIRST_OFFLINE_KEY)) return;
  const facts = offlineSetupFacts();
  if (facts.workerStart !== "cold" || facts.workerReadyMs == null) return;
  const bytes = await cachedByteTotal();
  writeJson(FIRST_OFFLINE_KEY, {
    readyMs: Math.round(performance.now()),
    bytes,
    retry: Boolean(facts.cacheRetry || report.serviceWorker?.retry),
  });
}

export async function paintReadiness() {
  const report = await diagnosticsSnapshot();
  const engineMissing = [...report.caches.pyodide.missing, ...report.caches.engine.missing];
  const engineNode = document.querySelector("#ready-engine");
  if (engineNode) {
    engineNode.textContent = engineMissing.length
      ? `Engine downloaded: no — ${engineMissing.join(", ")}`
      : "Engine downloaded: yes";
  }
  const racesNode = document.querySelector("#ready-races");
  if (racesNode) {
    const missing = report.caches.races.missing;
    racesNode.textContent = missing.length
      ? `Races cached: no — ${missing.join(", ")}`
      : "Races cached: yes";
  }
  const installed = isInstalled();
  const homeNode = document.querySelector("#ready-installed");
  if (homeNode) {
    homeNode.textContent = `Installed to Home Screen: ${installed ? "Yes" : "No"}`;
  }
  const protectedStorage = report.storage.persisted === true;
  const storageNode = document.querySelector("#ready-storage");
  if (storageNode) {
    storageNode.textContent = `Storage protected: ${protectedStorage ? "Yes" : "No"}`;
  }
  const evict = document.querySelector("#ready-evict");
  if (evict) {
    evict.classList.toggle("hidden", protectedStorage);
    evict.textContent = protectedStorage ? "" : EVICT_NOTE;
  }
  const verified = document.querySelector("#ready-verified");
  if (verified) {
    verified.textContent = `Last verified offline: ${formatStamp(verifiedAt())}`;
  }
  const line = document.querySelector("#offline-ready-line");
  if (line) {
    line.textContent = report.offlineReady
      ? "offline ready: yes"
      : `offline ready: no — ${report.missing.join(", ")}`;
  }
  await rememberOffline(report);
  if (verified) {
    verified.textContent = `Last verified offline: ${formatStamp(verifiedAt())}`;
  }
  paintDiag(report);
}

export function copyDiagnostics() {
  const box = document.querySelector("#diag-json");
  const note = document.querySelector("#diag-copy-note");
  const text = box?.value || "";
  if (!text) {
    if (note) note.textContent = "Nothing to copy yet.";
    return;
  }
  const done = (message) => {
    if (note) note.textContent = message;
  };
  if (navigator.clipboard?.writeText) {
    navigator.clipboard.writeText(text).then(() => done("Copied.")).catch(() => {
      box.focus();
      box.select();
      done("Select the text and copy it.");
    });
    return;
  }
  box.focus();
  box.select();
  done("Select the text and copy it.");
}

export function setupDiagnostics() {
  const enabled = new URLSearchParams(location.search).get("diag") === "1";
  const panel = document.querySelector("#diag-panel");
  if (!panel) return;
  panel.hidden = !enabled;
  if (!enabled) return;
  document.querySelector("#diag-copy")?.addEventListener("click", copyDiagnostics);
}

export function watchReadiness() {
  paintStaleBanner();
  paintReadiness().catch((error) => console.error(error));
  onEngineStatus(() => {
    paintReadiness().catch((error) => console.error(error));
  });
  setInterval(() => {
    const diag = document.querySelector("#diag-panel");
    const diagOpen = Boolean(diag && !diag.hidden);
    if (state.setupPane === "ready" || diagOpen) {
      paintReadiness().catch((error) => console.error(error));
    }
  }, 3000);
}
