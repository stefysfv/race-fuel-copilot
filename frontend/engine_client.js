/**
 * Plan and recalibrate through the browser worker.
 * Set ENGINE_MODE to "http" to post to the local FastAPI server instead.
 * `?engine=http` or `?engine=worker` overrides the flag for one page load.
 *
 * The worker starts at idle. A plan click before that finishes awaits the
 * same startup and then runs.
 */

import {
  ENGINE_FILES,
  isProductionAsset,
  PYODIDE_FILES,
  RACE_FILES,
  RUNTIME_CACHE,
  RUNTIME_FILES,
  SHELL_CACHE,
  SHELL_FILES,
} from "./runtime_manifest.js";

export const ENGINE_MODE = "worker";

const SHELL_WORKER = "/sw.js?v=23";
let catalogText = "";
let catalog = null;
const gpxByRace = new Map();
let worker = null;
let workerReady = null;
let offlineReady = null;
let engineReady = false;

const timings = {
  workerReadyMs: null,
  workerStart: null,
  lastPlanMs: null,
  lastRecalibrateMs: null,
};

const listeners = new Set();

function engineMode() {
  const query = new URLSearchParams(location.search).get("engine");
  if (query === "http" || query === "worker") {
    return query;
  }
  return ENGINE_MODE;
}

function emitStatus() {
  for (const listener of listeners) {
    try {
      listener();
    } catch (error) {
      console.error(error);
    }
  }
}

export function onEngineStatus(listener) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function engineIsReady() {
  if (engineMode() === "http") return true;
  return engineReady;
}

/** Start the worker after first paint. A click can start it sooner. */
export function startEngineWhenIdle() {
  const run = () => {
    prepareOffline().catch(() => {});
  };
  if (typeof window.requestIdleCallback === "function") {
    window.requestIdleCallback(run, { timeout: 1500 });
    return;
  }
  window.setTimeout(run, 200);
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

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
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

let cacheRetry = false;

async function fetchInto(cache, url) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      await cache.put(url, response.clone());
      if (attempt > 0) cacheRetry = true;
      return true;
    } catch (error) {
      if (attempt < 2) cacheRetry = true;
      await sleep(250 * (attempt + 1));
    }
  }
  return false;
}

async function cacheRuntime() {
  const cache = await caches.open(RUNTIME_CACHE);
  const files = RUNTIME_FILES.filter(isProductionAsset);
  const missing = [];
  let done = 0;
  for (const url of files) {
    const hit = await cache.match(url, { ignoreVary: true });
    if (!hit) {
      const ok = await fetchInto(cache, url);
      if (!ok) missing.push(url);
    }
    done += 1;
    paintPrep(Math.round((done / files.length) * 100), false);
  }
  if (missing.length) {
    throw new Error(`Could not download ${missing.join(", ")}.`);
  }
}

async function runtimeWasCached() {
  if (!("caches" in window)) return false;
  const cache = await caches.open(RUNTIME_CACHE);
  for (const url of RUNTIME_FILES) {
    if (!(await cache.match(url, { ignoreVary: true }))) return false;
  }
  return true;
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
    worker = new Worker("/engine.worker.js?v=31", { type: "module" });
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
    engineReady = true;
    emitStatus();
    return;
  }
  const started = performance.now();
  timings.workerStart = (await runtimeWasCached()) ? "cached" : "cold";
  paintPrep(0, false);
  if (navigator.storage?.persist) {
    navigator.storage.persist().catch(() => {});
  }
  await ensureServiceWorker();
  await cacheRuntime();
  await loadCatalog();
  await bootWorker();
  timings.workerReadyMs = Math.round(performance.now() - started);
  engineReady = true;
  paintPrep(100, true);
  emitStatus();
}

export function prepareOffline() {
  if (!offlineReady) {
    offlineReady = runPrepare().catch((error) => {
      offlineReady = null;
      engineReady = false;
      const node = prepNode();
      if (node) {
        node.dataset.state = "error";
        node.textContent = error?.message || "Offline mode could not be prepared.";
      }
      emitStatus();
      throw error;
    });
  }
  return offlineReady;
}

async function groupStatus(cacheName, urls) {
  if (!("caches" in window)) {
    return { total: urls.length, cached: 0, missing: urls.slice() };
  }
  const cache = await caches.open(cacheName);
  const missing = [];
  for (const url of urls) {
    if (!(await cache.match(url, { ignoreVary: true }))) missing.push(url);
  }
  return { total: urls.length, cached: urls.length - missing.length, missing };
}

function askServiceWorker() {
  return new Promise((resolve) => {
    const controller = navigator.serviceWorker?.controller;
    if (!controller) {
      resolve(null);
      return;
    }
    const channel = new MessageChannel();
    const timer = setTimeout(() => resolve(null), 800);
    channel.port1.onmessage = (event) => {
      clearTimeout(timer);
      resolve(event.data || null);
    };
    controller.postMessage({ type: "offline-status" }, [channel.port2]);
  });
}

async function storageReport() {
  const persisted = navigator.storage?.persisted
    ? await navigator.storage.persisted()
    : null;
  let estimate = null;
  if (navigator.storage?.estimate) {
    estimate = await navigator.storage.estimate();
  }
  return {
    persisted,
    quota: estimate?.quota ?? null,
    usage: estimate?.usage ?? null,
  };
}

export function offlineSetupFacts() {
  return {
    workerStart: timings.workerStart,
    workerReadyMs: timings.workerReadyMs,
    cacheRetry,
  };
}

export async function diagnosticsSnapshot() {
  const shellFiles = SHELL_FILES.filter(isProductionAsset);
  const [shell, races, pyodide, engine, serviceWorker, storage] = await Promise.all([
    groupStatus(SHELL_CACHE, shellFiles),
    groupStatus(SHELL_CACHE, RACE_FILES),
    groupStatus(RUNTIME_CACHE, PYODIDE_FILES),
    groupStatus(RUNTIME_CACHE, ENGINE_FILES),
    askServiceWorker(),
    storageReport(),
  ]);
  const missing = [...new Set([
    ...shell.missing,
    ...pyodide.missing,
    ...engine.missing,
  ])];
  return {
    workerReadyMs: timings.workerReadyMs,
    workerStart: timings.workerStart,
    lastPlanMs: timings.lastPlanMs,
    lastRecalibrateMs: timings.lastRecalibrateMs,
    offlineReady: missing.length === 0,
    missing,
    caches: { shell, races, pyodide, engine },
    serviceWorker,
    storage,
    userAgent: navigator.userAgent,
  };
}

export async function listRaces() {
  const payload = await loadCatalog();
  return payload.races || [];
}

async function timedWorkerCall(kind, type, request) {
  await prepareOffline();
  await loadCatalog();
  const gpxText = await gpxFor(request);
  const started = performance.now();
  const result = await callWorker(type, request, gpxText);
  const elapsed = Math.round(performance.now() - started);
  if (kind === "plan") timings.lastPlanMs = elapsed;
  if (kind === "recalibrate") timings.lastRecalibrateMs = elapsed;
  emitStatus();
  return result;
}

export async function inspectGpx(xml, track) {
  const request = { gpx_track: track || "longest" };
  if (engineMode() === "http") {
    return postJson("/api/inspect-gpx", { ...request, gpx_xml: xml });
  }
  await prepareOffline();
  await loadCatalog();
  return callWorker("inspect_gpx", request, xml);
}

export async function generatePlan(request) {
  if (engineMode() === "http") {
    return postJson("/api/generate-plan", request);
  }
  return timedWorkerCall("plan", "generate_plan", request);
}

export async function previewZones(request) {
  if (engineMode() === "http") {
    return postJson("/api/preview-zones", request);
  }
  return timedWorkerCall("preview", "preview_zones", request);
}

export async function recalibrate(request) {
  if (engineMode() === "http") {
    return postJson("/api/recalibrate", request);
  }
  return timedWorkerCall("recalibrate", "recalibrate", request);
}
