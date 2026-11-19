/** Stomach and terrain lines. Presets only fill numbers. The request sends numbers.

Default numbers are omitted so climb_sensitivity="sensitive" still moves the
full-gel climb line to +3.5%. A Sensitive or Iron stomach preset, or a slider
that has left the constants, sends the four grades.
*/

import { previewZones } from "./engine_client.js";
import { state } from "./state.js";

const STORAGE_KEY = "fuel-zone-profile";

/** Full gel down to, full gel up to, red below, red above. */
export const ZONE_PRESETS = {
  sensitive: { green_min_grade: -6, green_max_grade: 3.5, red_descent_grade: -10, red_climb_grade: 8 },
  default: { green_min_grade: -8, green_max_grade: 5, red_descent_grade: -12, red_climb_grade: 10 },
  iron: { green_min_grade: -10, green_max_grade: 8, red_descent_grade: -16, red_climb_grade: 15 },
};

const EDGE_KEYS = ["green_min_grade", "green_max_grade", "red_descent_grade", "red_climb_grade"];

const LIMITS = {
  green_min_grade: [-12, -3],
  green_max_grade: [2, 12],
  red_descent_grade: [-20, -8],
  red_climb_grade: [6, 20],
};

let previewTimer = 0;
let previewToken = 0;

function snap(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.round(number * 10) / 10;
}

function formatGrade(value) {
  const number = snap(value);
  if (number === null) return "—";
  const text = Number.isInteger(number) ? String(number) : number.toFixed(1);
  if (number > 0) return `+${text}%`;
  return `${text.replace("-", "−")}%`;
}

function sameEdges(left, right) {
  return EDGE_KEYS.every((key) => snap(left?.[key]) === snap(right?.[key]));
}

function presetName(edges) {
  if (sameEdges(edges, ZONE_PRESETS.sensitive)) return "sensitive";
  if (sameEdges(edges, ZONE_PRESETS.iron)) return "iron";
  if (sameEdges(edges, ZONE_PRESETS.default)) return "default";
  return "custom";
}

function clampField(profile, key) {
  const [low, high] = LIMITS[key];
  const number = snap(profile[key]);
  profile[key] = Math.min(high, Math.max(low, number === null ? ZONE_PRESETS.default[key] : number));
}

/** Keep a one-point yellow band. The slider the runner moved is the one that yields. */
function clampMoved(profile, key) {
  clampField(profile, key);
  if (key === "green_max_grade") {
    profile.green_max_grade = Math.min(profile.green_max_grade, profile.red_climb_grade - 1);
  } else if (key === "red_climb_grade") {
    profile.red_climb_grade = Math.max(profile.red_climb_grade, profile.green_max_grade + 1);
  } else if (key === "green_min_grade") {
    profile.green_min_grade = Math.max(profile.green_min_grade, profile.red_descent_grade + 1);
  } else if (key === "red_descent_grade") {
    profile.red_descent_grade = Math.min(profile.red_descent_grade, profile.green_min_grade - 1);
  }
  clampField(profile, key);
}

function ordered(profile) {
  return (
    profile.red_descent_grade + 1 <= profile.green_min_grade
    && profile.green_min_grade < 0
    && 0 < profile.green_max_grade
    && profile.green_max_grade <= profile.red_climb_grade - 1
  );
}

function profileFromStored(raw) {
  if (!raw || typeof raw !== "object") return null;
  const next = { ...ZONE_PRESETS.default };
  for (const key of EDGE_KEYS) {
    const [low, high] = LIMITS[key];
    const number = snap(raw[key]);
    if (number === null || number < low || number > high) return null;
    next[key] = number;
  }
  if (!ordered(next)) return null;
  next.preset = presetName(next);
  return next;
}

function persistZoneProfile() {
  try {
    const profile = state.zoneProfile;
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      preset: profile.preset,
      green_min_grade: profile.green_min_grade,
      green_max_grade: profile.green_max_grade,
      red_descent_grade: profile.red_descent_grade,
      red_climb_grade: profile.red_climb_grade,
    }));
  } catch {
    /* private browsing or a full disk: the sliders still work this visit */
  }
}

/** Numbers for the plan body. Default constants are left out on purpose. */
export function zoneFields(profile) {
  const current = profile || ZONE_PRESETS.default;
  if (sameEdges(current, ZONE_PRESETS.default)) return {};
  return {
    green_min_grade: current.green_min_grade,
    green_max_grade: current.green_max_grade,
    red_descent_grade: current.red_descent_grade,
    red_climb_grade: current.red_climb_grade,
  };
}

export function paintZoneProfile() {
  const profile = state.zoneProfile;
  for (const button of document.querySelectorAll("[data-zone-preset]")) {
    button.setAttribute("aria-checked", button.dataset.zonePreset === profile.preset ? "true" : "false");
  }
  for (const key of EDGE_KEYS) {
    const input = document.querySelector(`[data-zone-edge="${key}"]`);
    const readout = document.querySelector(`[data-zone-readout="${key}"]`);
    if (input) input.value = String(profile[key]);
    if (readout) readout.textContent = formatGrade(profile[key]);
  }
}

export function loadZoneProfile() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const stored = profileFromStored(JSON.parse(raw));
      if (stored) state.zoneProfile = stored;
    }
  } catch {
    /* keep the default lines */
  }
  paintZoneProfile();
}

export function applyZonePreset(name) {
  const edges = ZONE_PRESETS[name];
  if (!edges) return;
  state.zoneProfile = { preset: name, ...edges };
  paintZoneProfile();
  persistZoneProfile();
  scheduleZonePreview();
}

export function onZoneSlider(key, value) {
  if (!EDGE_KEYS.includes(key)) return;
  const next = { ...state.zoneProfile };
  next[key] = snap(value);
  clampMoved(next, key);
  next.preset = presetName(next);
  state.zoneProfile = next;
  paintZoneProfile();
  persistZoneProfile();
  scheduleZonePreview();
}

function previewRequest() {
  const athlete = {
    climb_sensitivity: state.gut.saved && state.gut.climb_sensitivity === "sensitive" ? "sensitive" : "normal",
    ...zoneFields(state.zoneProfile),
  };
  if (state.source === "gpx") {
    if (!state.gpxReady || !String(state.gpxXml || "").trim()) return null;
    const body = { athlete, gpx_xml: state.gpxXml, gpx_track: state.gpxTrack || "longest" };
    if (state.distanceOverrideKm > 0) body.distance_override_km = state.distanceOverrideKm;
    return body;
  }
  if (!state.raceId) return null;
  return { athlete, race_id: state.raceId };
}

function paintShare(text) {
  const readout = document.querySelector("#zone-red-share");
  if (readout) readout.textContent = text;
}

async function refreshZonePreview() {
  const token = ++previewToken;
  const body = previewRequest();
  if (!body) {
    paintShare("Red share of this course: —");
    return;
  }
  paintShare("Red share of this course: …");
  try {
    const result = await previewZones(body);
    if (token !== previewToken) return;
    const share = Number(result?.red_share_of_course_pct);
    paintShare(Number.isFinite(share) ? `Red share of this course: ${Math.round(share)}%` : "Red share of this course: —");
  } catch {
    if (token !== previewToken) return;
    paintShare("Red share of this course: —");
  }
}

export function scheduleZonePreview() {
  window.clearTimeout(previewTimer);
  previewTimer = window.setTimeout(() => {
    refreshZonePreview().catch(() => {});
  }, 250);
}
