/** Two caches. A new app shell must not evict the Pyodide runtime. */

import {
  isProductionAsset,
  RUNTIME_CACHE,
  RUNTIME_FILES,
  SHELL_CACHE,
  SHELL_FILES,
} from "./runtime_manifest.js";

/** Paths that failed the last install. A later cache check is the live status. */
let shellMissing = [];

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function putWithRetry(cache, url) {
  let lastError = "failed";
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(String(response.status));
      }
      await cache.put(url, response);
      return "";
    } catch (error) {
      lastError = error?.message || String(error);
      await sleep(250 * (attempt + 1));
    }
  }
  return lastError;
}

async function precacheShell() {
  const cache = await caches.open(SHELL_CACHE);
  const urls = SHELL_FILES.filter(isProductionAsset);
  const missing = [];
  for (const url of urls) {
    const hit = await cache.match(url, { ignoreVary: true });
    if (hit) continue;
    const error = await putWithRetry(cache, url);
    if (error) missing.push(url);
  }
  shellMissing = missing;
}

function isRuntime(url) {
  return RUNTIME_FILES.includes(url.pathname);
}

self.addEventListener("install", (event) => {
  event.waitUntil(precacheShell());
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

self.addEventListener("message", (event) => {
  if (event.data?.type !== "offline-status") return;
  const reply = {
    type: "offline-status",
    ready: shellMissing.length === 0,
    missing: shellMissing.slice(),
  };
  const port = event.ports && event.ports[0];
  if (port) {
    port.postMessage(reply);
    return;
  }
  event.source?.postMessage(reply);
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
    const cached = await cache.match(request, { ignoreVary: true })
      || await cache.match(new URL(request.url).pathname, { ignoreVary: true });
    if (cached) return cached;
    throw error;
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(RUNTIME_CACHE);
  const cached = await cache.match(request, { ignoreVary: true });
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
