/** Course map: the Leaflet view, the runner dot, and the real GPS dot. */

import { createMapView } from "./map_controller.js?v=21";
import { renderStopList } from "./stop_list.js";
import { snapToSegments } from "./offline_engine.js";
import { state, ui } from "./state.js";
import { setStatus } from "./ui_helpers.js";
import { addCustomAid } from "./food_menu.js";

export let mapView = null;

export function mapApi() {
  if (!mapView) {
    if (!window.L || !window.Chart) {
      throw new Error("The map libraries did not load. Reload while you have a connection.");
    }
    mapView = createMapView(document.querySelector("#map"), document.querySelector("#elevation-chart"));
  }
  return mapView;
}

export function drawMap() {
  if (!state.packedPlan) {
    ui.mapEmpty.classList.remove("hidden");
    ui.mapBlock.classList.add("hidden");
    renderStopList(null);
    return;
  }
  ui.mapEmpty.classList.add("hidden");
  ui.mapBlock.classList.remove("hidden");
  try {
    const plan = state.livePlan || state.packedPlan;
    renderStopList(plan);
    const view = mapApi();
    view.setCourse({
      segments: state.packedPlan.segments,
      aidStations: state.aidStations,
      fuelStops: plan.fuel_stops,
      forfeited: plan.forfeited || [],
    });
    const farOff = Boolean(state.realGps && state.snap && state.snap.offCourseM > 250);
    if (state.snap) {
      view.setRunner(state.snap.latitude, state.snap.longitude, !farOff);
    }
    if (state.realGps) {
      view.setRealLocation(state.realGps.latitude, state.realGps.longitude);
    }
  } catch (error) {
    setStatus(error.message);
  }
}

export function showRealDot() {
  if (!state.realGps || !state.packedPlan) {
    return;
  }
  try {
    mapApi().setRealLocation(state.realGps.latitude, state.realGps.longitude);
  } catch (error) {
    setStatus(error.message);
  }
}

export function moveRunner() {
  if (!mapView || !state.snap) {
    return;
  }
  mapView.setRunner(state.snap.latitude, state.snap.longitude);
}

export function onMapAidPick(latitude, longitude) {
  if (!state.packedPlan?.segments?.length) {
    setStatus("Build a plan before placing a station.");
    return;
  }
  const snap = snapToSegments(state.packedPlan.segments, latitude, longitude);
  const km = snap.distanceM / 1000;
  const typed = ui.aidName.value.trim();
  addCustomAid({
    name: typed || `Aid ${km.toFixed(1)}`,
    distanceKm: km,
    hasFood: ui.aidFood.checked,
    cutoffHours: Number(ui.aidCutoff.value),
  });
  ui.aidName.value = "";
}
