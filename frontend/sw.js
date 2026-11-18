/** Two caches. A new app shell must not evict the Pyodide runtime. */

import { RUNTIME_CACHE, RUNTIME_FILES } from "./runtime_manifest.js";

const SHELL_CACHE = "race-fuel-v17";
const SHELL = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/engine_client.js",
  "/runtime_manifest.js",
  "/map_controller.js",
  "/offline_db.js",
  "/offline_engine.js",
  "/manifest.json",
  "/data/races.json",
  "/data/gpx/salomon-vitosha-12.gpx",
  "/data/gpx/salomon-vitosha-27.gpx",
  "/data/gpx/salomon-vitosha-40.gpx",
  "/data/gpx/vitosha-100.gpx",
  "/vendor/leaflet.css",
  "/vendor/leaflet.js",
  "/vendor/chart.umd.min.js",
  "/vendor/images/marker-icon.png",
  "/vendor/images/marker-icon-2x.png",
  "/vendor/images/marker-shadow.png",
  "/vendor/images/layers.png",
  "/vendor/images/layers-2x.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png",
  "/icons/apple-touch-icon.png",
];

function isRuntime(url) {
  return RUNTIME_FILES.includes(url.pathname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys
        .filter((key) => key !== SHELL_CACHE && key !== RUNTIME_CACHE)
        .filter((key) => key.startsWith("race-fuel-v") || key.startsWith("race-fuel-pyodide-"))
        .map((key) => caches.delete(key)),
    )),
  );
  self.clients.claim();
});

async function networkFirst(request) {
  const cache = await caches.open(SHELL_CACHE);
  try {
    const response = await fetch(request);
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const cached = await cache.match(request) || await cache.match(new URL(request.url).pathname);
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(RUNTIME_CACHE);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client) {
          return client.focus();
        }
      }
      return self.clients.openWindow("/");
    }),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) {
    return;
  }
  if (isRuntime(url)) {
    event.respondWith(cacheFirst(event.request));
    return;
  }
  event.respondWith(networkFirst(event.request));
});
