/**
 * Main thread for the throwaway worker spike. No Python runs here.
 * Open /spike/ after scripts/build_engine_bundle.py.
 */

const CASES = [
  {
    label: "plan 40 km",
    type: "generate_plan",
    request: '{"race_id":"salomon-vitosha-40","athlete":{}}',
    gpx: "fixtures/salomon-vitosha-40.gpx",
    golden: "fixtures/golden/generate-plan__salomon-vitosha-40__default.json",
  },
  {
    label: "plan 100 km",
    type: "generate_plan",
    request: '{"race_id":"vitosha-100","athlete":{}}',
    gpx: "fixtures/vitosha-100.gpx",
    golden: "fixtures/golden/generate-plan__vitosha-100__default.json",
  },
  {
    label: "recalibrate 27 km",
    type: "recalibrate",
    request:
      '{"race_id":"salomon-vitosha-27","athlete":{},"latitude":42.5632102570075,"longitude":23.2704473114883,"elapsed_min":110.946,"gi_rescue":false}',
    gpx: "fixtures/salomon-vitosha-27.gpx",
    golden: "fixtures/golden/recalibrate__mid-race.json",
  },
];

const statusNode = document.querySelector("#status");
const logNode = document.querySelector("#log");
const sizeTable = document.querySelector("#sizes");
const timeTable = document.querySelector("#times");
const lines = [];

function log(line) {
  lines.push(line);
  logNode.textContent = lines.join("\n");
}

const ADDITIVE_KEYS = new Set(["reasons", "forfeited", "fuel_budget"]);

function stripAdditive(value) {
  if (Array.isArray(value)) return value.map(stripAdditive);
  if (value && typeof value === "object") {
    const stripped = {};
    for (const key of Object.keys(value)) {
      if (ADDITIVE_KEYS.has(key)) continue;
      stripped[key] = stripAdditive(value[key]);
    }
    return stripped;
  }
  return value;
}

function roundNumbers(value) {
  if (value === null || typeof value === "boolean" || typeof value === "string") {
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isFinite(value)) {
      return value;
    }
    return Number(value.toFixed(6));
  }
  if (Array.isArray(value)) {
    return value.map(roundNumbers);
  }
  if (typeof value === "object") {
    const rounded = {};
    for (const key of Object.keys(value)) {
      rounded[key] = roundNumbers(value[key]);
    }
    return rounded;
  }
  throw new Error(`Unexpected JSON value: ${typeof value}`);
}

function diffPaths(expected, actual, path = "$") {
  if (Array.isArray(expected) && Array.isArray(actual)) {
    const found = [];
    if (expected.length !== actual.length) {
      found.push([`${path}.length`, expected.length, actual.length]);
    }
    const count = Math.min(expected.length, actual.length);
    for (let index = 0; index < count; index += 1) {
      found.push(...diffPaths(expected[index], actual[index], `${path}[${index}]`));
    }
    return found;
  }
  if (expected && actual && typeof expected === "object" && typeof actual === "object" && !Array.isArray(expected) && !Array.isArray(actual)) {
    const found = [];
    const keys = Object.keys(expected).concat(Object.keys(actual).filter((key) => !(key in expected)));
    for (const key of keys) {
      const child = `${path}.${key}`;
      if (!(key in expected)) {
        found.push([child, "<missing>", actual[key]]);
      } else if (!(key in actual)) {
        found.push([child, expected[key], "<missing>"]);
      } else {
        found.push(...diffPaths(expected[key], actual[key], child));
      }
    }
    return found;
  }
  if (expected !== actual) {
    return [[path, expected, actual]];
  }
  return [];
}

function preview(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 160 ? `${text.slice(0, 157)}...` : text;
}

async function gzipSize(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream("gzip"));
  const compressed = await new Response(stream).arrayBuffer();
  return compressed.byteLength;
}

async function loadMeasured(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${url} HTTP ${response.status}`);
  }
  const buffer = await response.arrayBuffer();
  const name = url.split("/").pop();
  return {
    name,
    raw: buffer.byteLength,
    gzip: await gzipSize(buffer),
    text: new TextDecoder().decode(buffer),
  };
}

function callWorker(worker, type, payload) {
  const id = crypto.randomUUID();
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error(`${type} timed out`)), 180000);
    function onMessage(event) {
      if (!event.data || event.data.id !== id) {
        return;
      }
      clearTimeout(timeout);
      worker.removeEventListener("message", onMessage);
      resolve(event.data);
    }
    worker.addEventListener("message", onMessage);
    worker.postMessage({ id, type, payload });
  });
}

function fillTable(table, headers, rows) {
  table.replaceChildren();
  const head = document.createElement("tr");
  for (const header of headers) {
    const cell = document.createElement("th");
    cell.textContent = header;
    head.append(cell);
  }
  table.append(head);
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of row) {
      const cell = document.createElement("td");
      cell.textContent = value;
      tr.append(cell);
    }
    table.append(tr);
  }
}

function formatBytes(size) {
  return `${size.toLocaleString("en-US")} B`;
}

function formatMs(value) {
  return `${value.toFixed(1)} ms`;
}

async function main() {
  const params = new URLSearchParams(location.search);
  const runLabel = params.get("run") || "unlabeled";
  window.__spike = { status: "running", run: runLabel };
  statusNode.textContent = `Run ${runLabel}. Loading fixtures.`;
  const catalog = await loadMeasured("fixtures/races.json");
  const pageFiles = [
    await loadMeasured("index.html"),
    await loadMeasured("spike.js"),
    await loadMeasured("spike.css"),
  ];
  const cases = [];
  for (const item of CASES) {
    const gpx = await loadMeasured(item.gpx);
    const goldenFile = await loadMeasured(item.golden);
    cases.push({ ...item, gpx, golden: JSON.parse(goldenFile.text), goldenFile });
  }
  statusNode.textContent = `Run ${runLabel}. Starting the worker.`;
  const worker = new Worker("engine.worker.js", { type: "module" });
  const ready = await callWorker(worker, "ready", {});
  if (!ready.ok) {
    log(ready.error || "Worker failed to boot");
    statusNode.textContent = "Worker failed.";
    window.__spike = { status: "boot-failed", run: runLabel, error: ready.error, failed: ready.failed || [] };
    return;
  }

  const timings = [];
  const diffs = [];
  for (const item of cases) {
    statusNode.textContent = `Run ${runLabel}. ${item.label}.`;
    const reply = await callWorker(worker, item.type, {
      request_json: item.request,
      catalog_json: catalog.text,
      gpx_text: item.gpx.text,
    });
    if (!reply.ok) {
      log(`${item.label} failed: ${reply.error}`);
      timings.push({ label: item.label, ms: null, paths: null, error: reply.error });
      continue;
    }
    const actual = roundNumbers(stripAdditive(reply.result));
    const expected = roundNumbers(stripAdditive(item.golden));
    const found = diffPaths(expected, actual);
    timings.push({ label: item.label, ms: reply.elapsed_ms, paths: found.length, error: null });
    if (found.length) {
      log(`${item.label} differs in ${found.length} path(s).`);
      for (const [path, left, right] of found.slice(0, 12)) {
        log(`  ${path}`);
        log(`    snapshot: ${preview(left)}`);
        log(`    actual:   ${preview(right)}`);
      }
    } else {
      log(`${item.label} matches the golden snapshot. ${formatMs(reply.elapsed_ms)}.`);
    }
    diffs.push({ label: item.label, paths: found.length });
  }

  if (params.get("profile") === "1") {
    const heavy = cases[1];
    statusNode.textContent = `Run ${runLabel}. Profiling the 100 km plan.`;
    const profile = await callWorker(worker, "profile", {
      request_json: heavy.request,
      catalog_json: catalog.text,
      gpx_text: heavy.gpx.text,
    });
    log(profile.ok ? profile.result : profile.error);
  }

  const workerFiles = ready.result.downloads || [];
  const dataFiles = [catalog, ...pageFiles, ...cases.flatMap((item) => [item.gpx, item.goldenFile])];
  const allFiles = [...workerFiles, ...dataFiles];
  const rawTotal = allFiles.reduce((sum, file) => sum + file.raw, 0);
  const gzipTotal = allFiles.reduce((sum, file) => sum + file.gzip, 0);
  fillTable(
    sizeTable,
    ["File", "Raw", "Gzipped"],
    [
      ...allFiles.map((file) => [file.name, formatBytes(file.raw), formatBytes(file.gzip)]),
      ["Total", formatBytes(rawTotal), formatBytes(gzipTotal)],
    ],
  );
  fillTable(
    timeTable,
    ["Step", "Time", "Golden paths"],
    [
      ["Worker ready", formatMs(ready.elapsed_ms), ""],
      ...timings.map((item) => [item.label, item.ms === null ? item.error : formatMs(item.ms), item.paths === null ? "" : String(item.paths)]),
    ],
  );
  const failed = ready.result.failed || [];
  if (failed.length) {
    log(`Packages that failed to load: ${failed.join("; ")}`);
  } else {
    log("Every requested package loaded.");
  }
  log(`Versions ${JSON.stringify(ready.result.versions)}`);
  statusNode.textContent = `Run ${runLabel} finished.`;
  window.__spike = {
    status: "done",
    run: runLabel,
    ready_ms: ready.elapsed_ms,
    timings,
    diffs,
    raw_total: rawTotal,
    gzip_total: gzipTotal,
    files: allFiles.map((file) => ({ name: file.name, raw: file.raw, gzip: file.gzip })),
    failed,
    versions: ready.result.versions,
  };
}

main().catch((error) => {
  log(String(error));
  statusNode.textContent = "Spike failed.";
  window.__spike = { status: "error", error: String(error) };
});
