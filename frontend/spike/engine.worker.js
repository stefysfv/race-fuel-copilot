/**
 * Pyodide lives only in this worker. The page sends {id, type, payload}
 * and gets {id, ok, result} or {id, ok: false, error} back.
 *
 * Pyodide 314 rejects classic workers, so this file is loaded as a module.
 */

const INDEX_URL = new URL("../vendor/pyodide/", import.meta.url).href;
const ENGINE_ZIP = new URL("engine.zip", import.meta.url);

const downloads = [];
const pendingDownloads = [];
const failedPackages = [];
const originalFetch = self.fetch.bind(self);

function asUrl(input) {
  if (typeof input === "string") {
    return new URL(input, self.location.href);
  }
  if (input instanceof URL) {
    return input;
  }
  return new URL(input.url, self.location.href);
}

async function gzipSize(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
  const compressed = await new Response(stream).arrayBuffer();
  return compressed.byteLength;
}

function fileName(url) {
  const path = url.pathname.split("/");
  return path[path.length - 1] || url.href;
}

self.fetch = async function trackedFetch(input, init) {
  const url = asUrl(input);
  if (url.origin !== self.location.origin) {
    const message = `Blocked off-origin fetch ${url.href}`;
    failedPackages.push(message);
    throw new Error(message);
  }
  const response = await originalFetch(input, init);
  const recorded = response.clone().arrayBuffer().then(async (buffer) => {
    downloads.push({
      name: fileName(url),
      raw: buffer.byteLength,
      gzip: await gzipSize(buffer),
    });
  });
  pendingDownloads.push(recorded);
  return response;
};

async function finishDownloads() {
  const pending = pendingDownloads.splice(0, pendingDownloads.length);
  await Promise.all(pending);
}

async function measureModule(url) {
  const name = fileName(url);
  if (downloads.some((item) => item.name === name)) {
    return;
  }
  const response = await originalFetch(url, { cache: "force-cache" });
  const buffer = await response.arrayBuffer();
  downloads.push({
    name,
    raw: buffer.byteLength,
    gzip: await gzipSize(buffer),
  });
}

const startedAt = performance.now();
let pyodide = null;
let bootError = null;

async function boot() {
  const { loadPyodide } = await import(new URL("pyodide.mjs", INDEX_URL).href);
  pyodide = await loadPyodide({
    indexURL: INDEX_URL,
    packageBaseUrl: INDEX_URL,
  });
  try {
    await pyodide.loadPackage("pydantic");
  } catch (error) {
    failedPackages.push(`pydantic: ${error}`);
    throw error;
  }
  const zipResponse = await self.fetch(ENGINE_ZIP);
  if (!zipResponse.ok) {
    throw new Error(`engine.zip HTTP ${zipResponse.status}`);
  }
  const zipBytes = new Uint8Array(await zipResponse.arrayBuffer());
  pyodide.unpackArchive(zipBytes, "zip");
  pyodide.runPython(`
import pydantic
import pydantic_core
from backend.engine.entry import generate_plan_json, preview_zones_json, recalibrate_json
`);
  await measureModule(new URL("pyodide.mjs", INDEX_URL));
  await measureModule(new URL("pyodide.asm.mjs", INDEX_URL));
  await measureModule(new URL("engine.worker.js", import.meta.url));
  await finishDownloads();
}

const booted = boot().catch((error) => {
  bootError = error;
});

function pythonJson(source) {
  const text = pyodide.runPython(source);
  return JSON.parse(text);
}

const ENGINE_CALLS = {
  generate_plan: "generate_plan_json",
  recalibrate: "recalibrate_json",
  preview_zones: "preview_zones_json",
};

function runCall(kind) {
  const functionName = ENGINE_CALLS[kind];
  if (!functionName) {
    throw new Error(`Unknown message type ${kind}`);
  }
  return pythonJson(`
import json
from backend.engine.entry import ${functionName}
json.dumps(${functionName}(json.loads(request_json), json.loads(catalog_json), gpx_text))
`);
}

function runProfile() {
  return pyodide.runPython(`
import cProfile
import io
import json
import pstats

from backend.engine.entry import generate_plan_json

request = json.loads(request_json)
catalog = json.loads(catalog_json)
profile = cProfile.Profile()
profile.enable()
generate_plan_json(request, catalog, gpx_text)
profile.disable()
buffer = io.StringIO()
pstats.Stats(profile, stream=buffer).sort_stats("cumulative").print_stats(30)
buffer.getvalue()
`);
}

function assignInputs(payload) {
  pyodide.globals.set("request_json", payload.request_json);
  pyodide.globals.set("catalog_json", payload.catalog_json);
  pyodide.globals.set("gpx_text", payload.gpx_text);
}

async function handle(message) {
  await booted;
  if (bootError) {
    return { id: message.id, ok: false, error: String(bootError), failed: failedPackages };
  }
  const started = performance.now();
  try {
    if (message.type === "ready") {
      const versions = pythonJson(`
import json
import pydantic
import pydantic_core
json.dumps({
    "pyodide": "${"314.0.8"}",
    "pydantic": pydantic.__version__,
    "pydantic_core": pydantic_core.__version__,
})
`);
      return {
        id: message.id,
        ok: true,
        elapsed_ms: performance.now() - startedAt,
        result: {
          versions,
          downloads,
          failed: failedPackages,
        },
      };
    }
    assignInputs(message.payload || {});
    let result;
    if (ENGINE_CALLS[message.type]) {
      result = runCall(message.type);
    } else if (message.type === "profile") {
      result = runProfile();
    } else {
      throw new Error(`Unknown message type ${message.type}`);
    }
    return {
      id: message.id,
      ok: true,
      elapsed_ms: performance.now() - started,
      result,
    };
  } catch (error) {
    return { id: message.id, ok: false, error: String(error), failed: failedPackages };
  }
}

let chain = Promise.resolve();
self.onmessage = (event) => {
  const message = event.data;
  chain = chain.then(() => handle(message)).then(
    (reply) => self.postMessage(reply),
    (error) => self.postMessage({ id: message.id, ok: false, error: String(error) }),
  );
};
