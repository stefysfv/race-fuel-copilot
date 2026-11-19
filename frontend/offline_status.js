/** Offline checklist and the ?diag=1 panel. */

import { diagnosticsSnapshot, onEngineStatus } from "./engine_client.js";
import { state } from "./state.js";

export function cacheLine(group) {
  if (!group) return "—";
  if (!group.missing.length) return `${group.cached}/${group.total} cached`;
  return `${group.cached}/${group.total} cached; missing ${group.missing.join(", ")}`;
}

export function paintDiag(report) {
  const panel = document.querySelector("#diag-panel");
  if (!panel || panel.hidden) return;
  const box = document.querySelector("#diag-json");
  if (box) box.value = JSON.stringify(report, null, 2);
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
    ["Storage persisted", String(report.storage.persisted)],
    ["Storage estimate", `${report.storage.usage ?? "?"} of ${report.storage.quota ?? "?"}`],
    ["Offline ready", report.offlineReady ? "yes" : `no — ${report.missing.join(", ")}`],
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
  const storageNode = document.querySelector("#ready-storage");
  if (storageNode) {
    storageNode.textContent = `Storage persisted: ${report.storage.persisted ? "yes" : "no"}`;
  }
  const homeNode = document.querySelector("#ready-home");
  if (homeNode) {
    const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
    homeNode.textContent = standalone
      ? "Home Screen: this copy is open from the Home Screen."
      : "Home Screen: add Fuel Co-Pilot to your Home Screen. iOS can delete data for a site it treats as unused. This line is a hint.";
  }
  const line = document.querySelector("#offline-ready-line");
  if (line) {
    line.textContent = report.offlineReady
      ? "offline ready: yes"
      : `offline ready: no — ${report.missing.join(", ")}`;
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
