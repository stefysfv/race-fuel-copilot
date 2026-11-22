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
      gpxTrack: state.gpxTrack,
      distanceOverrideKm: state.distanceOverrideKm,
      waypointAids: state.waypointAids,
      gpxReport: state.gpxReport,
      carbs: state.carbs,
      weight: String(state.weightKg),
      temperature: String(state.temperatureC),
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
      pausedAt: state.pausedAt,
      pausedTotalMs: state.pausedTotalMs,
      intakeHistory: state.intakeHistory,
      completedLegs: state.completedLegs,
      giRescueEvents: state.giRescueEvents,
      passedDistanceM: state.passedDistanceM,
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
  state.gpxTrack = session.gpxTrack || "longest";
  state.distanceOverrideKm = session.distanceOverrideKm ?? null;
  state.waypointAids = Array.isArray(session.waypointAids) ? session.waypointAids : [];
  state.gpxReport = session.gpxReport || null;
  state.gpxReady = Boolean(session.gpxXml && session.gpxReport);
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
  state.pausedAt = session.pausedAt ?? null;
  state.pausedTotalMs = Number(session.pausedTotalMs) || 0;
  state.intakeHistory = Array.isArray(session.intakeHistory) ? session.intakeHistory : [];
  state.completedLegs = Array.isArray(session.completedLegs) ? session.completedLegs : [];
  state.giRescueEvents = Array.isArray(session.giRescueEvents) ? session.giRescueEvents : [];
  state.passedDistanceM = Number(session.passedDistanceM) || 0;
  state.giRescue = Boolean(session.giRescue);
  state.snap = session.snap;
  state.alerts = Boolean(session.alerts);
  const savedWeight = Number(session.weight);
  const savedTemp = Number(session.temperature);
  state.weightKg = Number.isFinite(savedWeight) && savedWeight > 30 && savedWeight < 250 ? savedWeight : 70;
  state.temperatureC = Number.isFinite(savedTemp) && savedTemp >= -20 && savedTemp <= 45 ? savedTemp : 18;
  ui.weight.value = String(state.weightKg);
  ui.temperature.value = String(state.temperatureC);
  ui.hours.value = session.hours ?? "5";
  ui.minutes.value = session.minutes ?? "30";
  return true;
}
