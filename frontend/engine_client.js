/**
 * Plan and recalibrate through the browser worker.
 * Set ENGINE_MODE to "http" to post to the local FastAPI server instead.
 * `?engine=http` or `?engine=worker` overrides the flag for one page load.
 */

import { RUNTIME_CACHE, RUNTIME_FILES } from "./runtime_manifest.js";

export const ENGINE_MODE = "worker";

const SHELL_WORKER = "/sw.js?v=17";
let catalogText = "";
let catalog = null;
const gpxByRace = new Map();
let worker = null;
let workerReady = null;
let offlineReady = null;

function engineMode() {
  const query = new URLSearchParams(location.search).get("engine");
  if (query === "http" || query === "worker") {
    return query;
  }
  return ENGINE_MODE;
}

function rejectionMessage(payload, status) {
  const detail = payload?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail)) {
    const lines = detail.slice(0, 3).map((item) => {
      const loc = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
      const msg = item?.msg || "Invalid value";
      return loc ? `${loc}: ${msg}` : msg;
    }).filter(Boolean);
    if (lines.length) return lines.join(" ");
  }
  return `The server rejected that request (${status}).`;
}

async function postJson(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw new Error("No connection to the fueling server.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(rejectionMessage(payload, response.status));
  }
  return payload;
}

function prepNode() {
  return document.querySelector("#offline-prep");
}

function paintPrep(percent, ready) {
  const node = prepNode();
  if (!node) return;
  node.replaceChildren();
  if (!ready) {
    node.dataset.state = "working";
    node.textContent = `Preparing offline mode... ${percent}%`;
    return;
  }
  node.dataset.state = "ready";
  node.textContent = "Ready for offline";
  const standalone = window.matchMedia("(display-mode: standalone)").matches || window.navigator.standalone === true;
  if (standalone) return;
  const hint = document.createElement("span");
  hint.className = "offline-hint";
  hint.textContent = "Add Fuel Co-Pilot to your Home Screen. iOS can delete data for a site it treats as unused.";
  node.append(hint);
}

function paintHttpMode() {
  const node = prepNode();
  if (!node) return;
  node.dataset.state = "ready";
  node.textContent = "Using the local fueling server.";
}

async function ensureServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  await navigator.serviceWorker.register(SHELL_WORKER, { type: "module" });
  await navigator.serviceWorker.ready;
  if (navigator.serviceWorker.controller) return;
  await new Promise((resolve) => {
    navigator.serviceWorker.addEventListener("controllerchange", () => resolve(), { once: true });
  });
}

async function cacheRuntime() {
  const cache = await caches.open(RUNTIME_CACHE);
  let done = 0;
  for (const url of RUNTIME_FILES) {
    const hit = await cache.match(url);
    if (!hit) {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Could not download ${url} (${response.status}).`);
      }
      await cache.put(url, response.clone());
    }
    done += 1;
    paintPrep(Math.round((done / RUNTIME_FILES.length) * 100), false);
  }
}

function callWorker(type, request, gpxText) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("The fueling engine timed out.")), 180000);
    function onMessage(event) {
      if (!event.data || event.data.id !== id) return;
      clearTimeout(timeout);
      worker.removeEventListener("message", onMessage);
      if (!event.data.ok) {
        reject(new Error(event.data.error || "The fueling engine failed."));
        return;
      }
      resolve(event.data.result);
    }
    worker.addEventListener("message", onMessage);
    worker.postMessage({
      id,
      type,
      payload: {
        request_json: JSON.stringify(request),
        catalog_json: catalogText,
        gpx_text: gpxText,
      },
    });
  });
}

function bootWorker() {
  if (!workerReady) {
    worker = new Worker("/engine.worker.js", { type: "module" });
    workerReady = callWorker("ready", {}, null).catch((error) => {
      workerReady = null;
      throw error;
    });
  }
  return workerReady;
}

async function loadCatalog() {
  if (catalogText) return catalog;
  const response = await fetch("/data/races.json");
  if (!response.ok) {
    throw new Error("Could not load the race list.");
  }
  catalogText = await response.text();
  catalog = JSON.parse(catalogText);
  return catalog;
}

async function gpxFor(request) {
  if (typeof request.gpx_xml === "string" && request.gpx_xml.trim()) {
    return request.gpx_xml;
  }
  const race = (catalog?.races || []).find((item) => item.id === request.race_id);
  if (!race?.gpx_file) return null;
  if (gpxByRace.has(race.id)) return gpxByRace.get(race.id);
  const response = await fetch(`/${race.gpx_file}`);
  if (!response.ok) {
    throw new Error(`Could not load the course for ${race.id}.`);
  }
  const text = await response.text();
  gpxByRace.set(race.id, text);
  return text;
}

async function runPrepare() {
  if (engineMode() === "http") {
    paintHttpMode();
    return;
  }
  paintPrep(0, false);
  if (navigator.storage?.persist) {
    navigator.storage.persist().catch(() => {});
  }
  await ensureServiceWorker();
  await cacheRuntime();
  await loadCatalog();
  await bootWorker();
  paintPrep(100, true);
}

export function prepareOffline() {
  if (!offlineReady) {
    offlineReady = runPrepare().catch((error) => {
      offlineReady = null;
      const node = prepNode();
      if (node) {
        node.dataset.state = "error";
        node.textContent = error?.message || "Offline mode could not be prepared.";
      }
      throw error;
    });
  }
  return offlineReady;
}

export async function listRaces() {
  const payload = await loadCatalog();
  return payload.races || [];
}

export async function generatePlan(request) {
  if (engineMode() === "http") {
    return postJson("/api/generate-plan", request);
  }
  await prepareOffline();
  await loadCatalog();
  return callWorker("generate_plan", request, await gpxFor(request));
}

export async function recalibrate(request) {
  if (engineMode() === "http") {
    return postJson("/api/recalibrate", request);
  }
  await prepareOffline();
  await loadCatalog();
  return callWorker("recalibrate", request, await gpxFor(request));
}
