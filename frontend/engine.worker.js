/**
 * Pyodide lives only in this worker. The page sends {id, type, payload}
 * and gets {id, ok, result} or {id, ok: false, error} back.
 *
 * Pyodide 314 rejects classic workers, so this file is loaded as a module.
 */

const INDEX_URL = new URL("vendor/pyodide/", self.location.href).href;
const ENGINE_ZIP = new URL("engine.zip", self.location.href);

let pyodide = null;
let bootError = null;

async function boot() {
  const { loadPyodide } = await import(new URL("pyodide.mjs", INDEX_URL).href);
  pyodide = await loadPyodide({
    indexURL: INDEX_URL,
    packageBaseUrl: INDEX_URL,
  });
  await pyodide.loadPackage("pydantic");
  const zipResponse = await fetch(ENGINE_ZIP);
  if (!zipResponse.ok) {
    throw new Error(`engine.zip HTTP ${zipResponse.status}`);
  }
  pyodide.unpackArchive(new Uint8Array(await zipResponse.arrayBuffer()), "zip");
  pyodide.runPython(
    "from backend.engine.entry import generate_plan_json, preview_zones_json, recalibrate_json",
  );
}

const ENGINE_CALLS = {
  generate_plan: "generate_plan_json",
  recalibrate: "recalibrate_json",
  preview_zones: "preview_zones_json",
};

const booted = boot().catch((error) => {
  bootError = error;
});

function runCall(kind) {
  const functionName = ENGINE_CALLS[kind];
  if (!functionName) {
    throw new Error(`Unknown message type ${kind}`);
  }
  const text = pyodide.runPython(`
import json
from backend.engine.entry import ${functionName}
json.dumps(${functionName}(json.loads(request_json), json.loads(catalog_json), gpx_text))
`);
  return JSON.parse(text);
}

async function handle(message) {
  await booted;
  if (bootError) {
    return { id: message.id, ok: false, error: String(bootError) };
  }
  try {
    if (message.type === "ready") {
      return { id: message.id, ok: true, result: { ready: true } };
    }
    const payload = message.payload || {};
    pyodide.globals.set("request_json", payload.request_json);
    pyodide.globals.set("catalog_json", payload.catalog_json);
    pyodide.globals.set("gpx_text", payload.gpx_text);
    if (!ENGINE_CALLS[message.type]) {
      throw new Error(`Unknown message type ${message.type}`);
    }
    return { id: message.id, ok: true, result: runCall(message.type) };
  } catch (error) {
    return { id: message.id, ok: false, error: String(error) };
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
