/** Files that stay cached across app-shell updates. Versions match frontend/vendor/pyodide/VERSIONS.json. */

export const RUNTIME_CACHE = "race-fuel-pyodide-314.0.8";

export const SHELL_CACHE = "race-fuel-v20";

export const RUNTIME_FILES = [
  "/vendor/pyodide/pyodide.mjs",
  "/vendor/pyodide/pyodide.asm.mjs",
  "/vendor/pyodide/pyodide.asm.wasm",
  "/vendor/pyodide/python_stdlib.zip",
  "/vendor/pyodide/pyodide-lock.json",
  "/vendor/pyodide/pydantic-2.12.5-py3-none-any.whl",
  "/vendor/pyodide/pydantic_core-2.41.5-cp314-cp314-pyemscripten_2026_0_wasm32.whl",
  "/vendor/pyodide/annotated_types-0.7.0-py3-none-any.whl",
  "/vendor/pyodide/typing_extensions-4.15.0-py3-none-any.whl",
  "/vendor/pyodide/typing_inspection-0.4.2-py3-none-any.whl",
  "/engine.zip",
  "/engine.worker.js",
];

export const ENGINE_FILES = ["/engine.zip", "/engine.worker.js"];

export const PYODIDE_FILES = RUNTIME_FILES.filter((url) => !ENGINE_FILES.includes(url));

export const RACE_FILES = [
  "/data/races.json",
  "/data/gpx/salomon-vitosha-12.gpx",
  "/data/gpx/salomon-vitosha-27.gpx",
  "/data/gpx/salomon-vitosha-40.gpx",
  "/data/gpx/vitosha-100.gpx",
];

export const SHELL_FILES = [
  "/",
  "/index.html",
  "/style.css",
  "/app.js",
  "/build_plan.js",
  "/navigation.js",
  "/gps.js",
  "/alerts.js",
  "/plan_request.js",
  "/elevation_chart.js",
  "/reason_strings.js",
  "/stop_list.js",
  "/map_screen.js",
  "/live.js",
  "/plan_cursor.js",
  "/packing.js",
  "/food_menu.js",
  "/session.js",
  "/offline_status.js",
  "/ui_helpers.js",
  "/state.js",
  "/engine_client.js",
  "/runtime_manifest.js",
  "/map_controller.js",
  "/offline_db.js",
  "/offline_engine.js",
  "/manifest.json",
  ...RACE_FILES,
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

/** Spike pages and golden fixtures stay out of the production precache. */
export function isProductionAsset(url) {
  const path = String(url).toLowerCase();
  return !path.includes("/spike/") && !path.includes("golden");
}
