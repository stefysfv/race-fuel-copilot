/** Saves and restores the race session in IndexedDB. */

import { STORAGE_SCHEMA_VERSION, state, ui } from "./state.js";
import { setStatus } from "./ui_helpers.js";
import { saveSession } from "./offline_db.js";

export function resetOutdatedStorage() {
  try {
    if (localStorage.getItem("fuel_schema_version") !== STORAGE_SCHEMA_VERSION) {
      localStorage.clear();
      localStorage.setItem("fuel_schema_version", STORAGE_SCHEMA_VERSION);
    }
  } catch (error) {
    console.error(error);
  }
}

export async function persist() {
  try {
    await saveSession({
      source: state.source,
      raceId: state.raceId,
      gpxXml: state.gpxXml,
      gpxName: state.gpxName,
      carbs: state.carbs,
      weight: ui.weight.value,
      temperature: ui.temperature.value,
      hours: ui.hours.value,
      minutes: ui.minutes.value,
      targetEdited: state.targetEdited,
      aidStations: state.aidStations,
      customAids: state.customAids,
      packedPlan: state.packedPlan,
      livePlan: state.livePlan,
      locker: state.locker,
      fuelPreference: state.fuelPreference,
      logged: state.logged,
      startedAt: state.startedAt,
      giRescue: state.giRescue,
      snap: state.snap,
      alerts: state.alerts,
    });
  } catch (error) {
    setStatus("The plan is on screen, but it could not be saved on this phone.");
  }
}

/** Copies a saved session into state. Returns false when there is nothing to restore. */
export function restore(session) {
  if (!session) {
    return false;
  }
  state.source = session.source || "race";
  state.raceId = session.raceId || state.raceId;
  state.gpxXml = session.gpxXml || "";
  state.gpxName = session.gpxName || "";
  state.carbs = session.carbs || 60;
  state.targetEdited = Boolean(session.targetEdited);
  state.aidStations = session.aidStations || [];
  state.customAids = Array.isArray(session.customAids) ? session.customAids : [];
  state.packedPlan = session.packedPlan;
  state.livePlan = session.livePlan;
  state.logged = session.logged || [];
  state.locker = Array.isArray(session.locker) ? session.locker : [];
  state.fuelPreference = session.fuelPreference || "gels_first";
  state.startedAt = session.startedAt;
  state.giRescue = Boolean(session.giRescue);
  state.snap = session.snap;
  state.alerts = Boolean(session.alerts);
  ui.weight.value = session.weight ?? "70";
  ui.temperature.value = session.temperature ?? "18";
  ui.hours.value = session.hours ?? "5";
  ui.minutes.value = session.minutes ?? "30";
  return true;
}
