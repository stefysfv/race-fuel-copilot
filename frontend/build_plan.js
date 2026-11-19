/** Build Plan: scale a flat pace so the predicted finish matches the typed target. */

import { engineIsReady, generatePlan, onEngineStatus } from "./engine_client.js";
import { armGpsWatch, resetSim } from "./gps.js";
import { renderLive } from "./live.js";
import { drawMap } from "./map_screen.js";
import { showSetupPane } from "./navigation.js";
import { renderPacking } from "./packing.js";
import { courseBody, readAthlete, readTargetMin, selectedRace } from "./plan_request.js";
import { persist } from "./session.js";
import { state } from "./state.js";
import { formatDuration, setStatus } from "./ui_helpers.js";

export let buildingPlan = false;

export async function buildPlan(event) {
  event?.preventDefault?.();
  if (buildingPlan) return;
  buildingPlan = true;
  state.autoGps = true;
  const buildBtn = document.querySelector("#build-plan");
  const starting = !engineIsReady();
  if (buildBtn) {
    buildBtn.disabled = true;
    if (starting) buildBtn.textContent = "Starting engine...";
  }
  let stopWatch = () => {};
  if (starting) {
    stopWatch = onEngineStatus(() => {
      if (engineIsReady() && buildBtn) buildBtn.textContent = "Building the plan…";
    });
  }
  try {
    armGpsWatch();
    const targetMin = readTargetMin();
    setStatus(starting ? "Starting engine..." : "Building the plan…");
    const probe = await generatePlan(courseBody(readAthlete(8)));
    let pace = 8 * (targetMin / probe.predicted_duration_min);
    pace = Math.min(19.9, Math.max(3.51, pace));
    const plan = await generatePlan(courseBody(readAthlete(Number(pace.toFixed(3)))));
    state.packedPlan = plan;
    state.livePlan = plan;
    state.logged = [];
    state.startedAt = null;
    state.giRescue = false;
    state.snap = null;
    resetSim();
    state.aidStations = plan.aid_stations?.length
      ? plan.aid_stations
      : (state.source === "race" ? (selectedRace()?.aid_stations || []) : []);
    const missed = Math.abs(plan.predicted_duration_min - targetMin) > 1;
    setStatus(missed
      ? `Plan ready. The closest effort pace the engine allows finishes in ${formatDuration(plan.predicted_duration_min)}.`
      : "Plan ready. Packing splits are below.");
    renderPacking();
    showSetupPane("packing");
    renderLive();
    drawMap();
    await persist();
  } catch (error) {
    console.error(error);
    setStatus(error?.message || "Could not build the plan.", true);
  } finally {
    stopWatch();
    buildingPlan = false;
    const buildBtn = document.querySelector("#build-plan");
    if (buildBtn) {
      buildBtn.disabled = false;
      buildBtn.textContent = "Build Plan";
    }
  }
}
