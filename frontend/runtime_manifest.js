/** Files that stay cached across app-shell updates. Versions match frontend/vendor/pyodide/VERSIONS.json. */

export const RUNTIME_CACHE = "race-fuel-pyodide-314.0.8";

export const SHELL_CACHE = "race-fuel-v55";

/**
 * sha256 of every SHELL_FILES body, LF-normalized, in list order.
 * This line is left out of the hash so the value can name itself.
 * tests/test_shell_cache.py recomputes it.
 */
export const SHELL_FILES_HASH = "b46221a29d399812433a8d0a2a0351cf727252e67ad44190c3c028274c20d1b8";

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
  "/engine.zip?v=003b31c8",
  "/engine.worker.js?v=003b31c8",
];

export const ENGINE_FILES = ["/engine.zip?v=003b31c8", "/engine.worker.js?v=003b31c8"];

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
  "/leg_projection.js",
  "/plan_request.js",
  "/zone_profile.js",
  "/elevation_chart.js",
  "/reason_strings.js",
  "/stop_list.js",
  "/map_screen.js",
  "/live.js",
  "/plan_cursor.js",
  "/packing.js",
  "/fuel_budget.js",
  "/food_menu.js",
  "/session.js",
  "/offline_status.js",
  "/install_prompt.js",
  "/feedback.js",
  "/local_store.js",
  "/units.js",
  "/crew_sheet.js",
  "/gps_guard.js",
  "/battery_diag.js",
  "/version.js",
  "/privacy.html",
  "/terms.html",
  "/ui_helpers.js",
  "/state.js",
  "/engine_client.js",
  "/runtime_manifest.js",
  "/map_controller.js",
  "/offline_db.js",
  "/gpx_upload.js",
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

/** Fixture snapshots stay out of the production precache. */
export function isProductionAsset(url) {
  return !String(url).toLowerCase().includes("golden");
}
