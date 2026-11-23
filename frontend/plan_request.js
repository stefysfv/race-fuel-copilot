/** Form values that become an engine plan request. */

import { listRaces } from "./engine_client.js";
import { customFoodPayload, packedVestPayload, renderGutPill } from "./food_menu.js";
import { DEFAULT_TARGETS, state, ui } from "./state.js";
import { clampCarbs, paintMinutes } from "./ui_helpers.js";
import { zoneFields } from "./zone_profile.js";
import {
  distanceUnit,
  formatDistance,
  formatTempInputValue,
  formatWeightInputValue,
  getUnitSystem,
  isImperial,
  tempFromDisplay,
  tempUnit,
  weightFromDisplay,
  weightUnit,
} from "./units.js";

export function readTargetMin() {
  const hours = Number(ui.hours.value);
  const minutes = Number(ui.minutes.value);
  if (!Number.isFinite(hours) || !Number.isFinite(minutes)) {
    throw new Error("Enter a target finish in hours and minutes.");
  }
  const total = hours * 60 + minutes;
  if (total < 30 || total > 47 * 60 + 59) {
    throw new Error("Target finish must be between 30 minutes and 48 hours.");
  }
  return total;
}

export function syncWeightFromInput() {
  const raw = Number(ui.weight.value);
  if (!Number.isFinite(raw)) {
    state.weightKg = NaN;
    return;
  }
  state.weightKg = weightFromDisplay(raw);
}

export function syncTempFromInput() {
  const raw = Number(ui.temperature.value);
  if (!Number.isFinite(raw)) {
    state.temperatureC = NaN;
    return;
  }
  state.temperatureC = tempFromDisplay(raw);
}

export function paintUnitInputs() {
  const imperial = isImperial();
  const wLabel = document.querySelector("#weight-label");
  const tLabel = document.querySelector("#temp-label");
  const oLabel = document.querySelector("#distance-override-label");
  if (wLabel) wLabel.textContent = `Weight (${weightUnit()})`;
  if (tLabel) tLabel.textContent = `Temp (${tempUnit()})`;
  if (oLabel) oLabel.textContent = `Override declared distance (${distanceUnit()})`;
  if (ui.weight) {
    ui.weight.min = imperial ? "68" : "31";
    ui.weight.max = imperial ? "549" : "249";
    if (Number.isFinite(state.weightKg)) {
      ui.weight.value = formatWeightInputValue(state.weightKg);
    }
  }
  if (ui.temperature) {
    ui.temperature.min = imperial ? "-4" : "-20";
    ui.temperature.max = imperial ? "113" : "45";
    if (Number.isFinite(state.temperatureC)) {
      ui.temperature.value = formatTempInputValue(state.temperatureC);
    }
  }
  if (ui.aidKm) {
    ui.aidKm.placeholder = imperial ? "Mi" : "Km";
    ui.aidKm.setAttribute("aria-label", imperial ? "Aid station miles" : "Aid station kilometers");
  }
  for (const button of document.querySelectorAll(".unit-btn")) {
    button.setAttribute("aria-checked", button.dataset.unit === getUnitSystem() ? "true" : "false");
  }
}

export function paintRaceOptions() {
  if (!ui.raceSelect || !state.races.length) return;
  const current = state.raceId;
  ui.raceSelect.replaceChildren();
  for (const race of state.races) {
    const option = document.createElement("option");
    option.value = race.id;
    option.textContent = `${race.name} · ${formatDistance(race.distance_km, { digits: isImperial() ? 1 : 0 })}`;
    ui.raceSelect.append(option);
  }
  if (current) ui.raceSelect.value = current;
}

export function readAthlete(pace) {
  const weight = Number(state.weightKg);
  const temperature = Number(state.temperatureC);
  if (!(weight > 30 && weight < 250)) {
    throw new Error(isImperial()
      ? "Weight must be between 67 and 550 lb."
      : "Weight must be between 30 and 250 kg.");
  }
  if (!(temperature >= -20 && temperature <= 45)) {
    throw new Error(isImperial()
      ? "Temperature must be between −4 and 113 °F."
      : "Temperature must be between −20 and 45 °C.");
  }
  const athlete = {
    weight_kg: weight,
    temperature_c: temperature,
    carbs_g_per_hour: state.carbs,
    flat_pace_min_per_km: pace,
    fuel_preference: ["gels_first", "balanced_real_food", "real_food_heavy"].includes(state.fuelPreference)
      ? state.fuelPreference
      : "gels_first",
    target_time_minutes: readTargetMin(),
    vest_foods: [...state.vestFoods],
    station_foods: [...state.stationFoods],
    custom_vest_items: customFoodPayload("vest"),
    custom_station_items: customFoodPayload("station"),
    carb_multiplier: state.gut.saved ? state.gut.carb_multiplier : 1,
    climb_sensitivity: state.gut.saved ? state.gut.climb_sensitivity : "normal",
    early_savoury: state.gut.saved ? state.gut.early_savoury : false,
    has_salt_caps: state.saltOn,
    salt_caps_qty: state.saltOn ? Math.min(40, parseInt(state.saltQty, 10) || 0) : 0,
    salt_cap_mg: Math.min(2000, parseInt(state.saltMg, 10) || 200),
    flask_count: Math.min(6, Math.max(0, parseInt(state.flaskQty, 10) || 0)),
    ...zoneFields(state.zoneProfile),
  };
  const locker = [];
  for (const item of Array.isArray(state.locker) ? state.locker : []) {
    const name = String(item?.name || "").trim().slice(0, 60);
    const carbs = clampCarbs(item?.carbs_g);
    if (!name || !(carbs > 0)) continue;
    const kind = item?.kind === "liquid" || item?.kind === "bar" ? item.kind : "gel";
    locker.push({
      name,
      carbs_g: carbs,
      caffeine_mg: Math.min(400, Math.max(0, Number(item?.caffeine_mg) || 0)),
      kind,
    });
    if (locker.length >= 12) break;
  }
  if (locker.length) athlete.locker = locker;
  return athlete;
}

export function courseBody(athlete) {
  const body = { athlete, packed_vest_items: packedVestPayload() };
  const aids = uploadAids();
  if (aids.length) body.aid_stations = aids;
  if (state.source === "gpx") {
    if (!state.gpxXml.trim()) {
      throw new Error("Choose a GPX file first.");
    }
    if (!state.gpxReady) {
      throw new Error(state.gpxError || "This GPX is still being checked.");
    }
    body.gpx_xml = state.gpxXml;
    body.gpx_track = state.gpxTrack || "longest";
    if (state.distanceOverrideKm > 0) body.distance_override_km = state.distanceOverrideKm;
    return body;
  }
  if (!state.raceId) {
    throw new Error("Choose a race first.");
  }
  body.race_id = state.raceId;
  return body;
}

function uploadAids() {
  const fromFile = (Array.isArray(state.waypointAids) ? state.waypointAids : [])
    .filter((station) => station.checked && station.name && Number.isFinite(Number(station.distance_km)))
    .map((station) => ({
      name: String(station.name).slice(0, 80),
      distance_km: Number(station.distance_km),
      has_water: station.has_water !== false,
      has_food: Boolean(station.has_food),
      drop_bag: Boolean(station.drop_bag),
    }));
  const taken = new Set(fromFile.map((station) => station.name.toLowerCase()));
  const typed = [];
  for (const station of state.customAids) {
    const name = String(station?.name || "").trim();
    if (!name || taken.has(name.toLowerCase())) continue;
    const row = {
      name,
      distance_km: Number(station.distance_km),
      has_water: station.has_water !== false,
      has_food: Boolean(station.has_food),
      drop_bag: Boolean(station.drop_bag),
    };
    if (station.cutoff_hours) row.cutoff_hours = station.cutoff_hours;
    typed.push(row);
    taken.add(name.toLowerCase());
  }
  return [...fromFile, ...typed];
}

export function selectedRace() {
  return state.races.find((race) => race.id === state.raceId) || null;
}

export function applyTargetDefault() {
  if (state.targetEdited) {
    return;
  }
  const preset = DEFAULT_TARGETS[state.raceId] || { hours: 6, minutes: 0 };
  ui.hours.value = String(preset.hours);
  ui.minutes.value = String(preset.minutes);
  paintMinutes();
}

export function paintAidDrawer() {
  const show = true;
  ui.aidDrawer?.classList.toggle("hidden", !show);
  if (show && ui.aidDrawer) ui.aidDrawer.open = true;
  const toggle = document.querySelector("#edit-stations");
  if (toggle) toggle.textContent = show && state.source !== "gpx" ? "Hide Stations" : "Edit Stations";
}

export function setSource() {
  state.source = "gpx";
  paintAidDrawer();
}

export function setCarbs(grams) {
  state.carbs = grams;
  for (const button of document.querySelectorAll(".gut")) {
    button.setAttribute("aria-checked", Number(button.dataset.carbs) === grams ? "true" : "false");
  }
  renderGutPill();
}

export async function loadRaces() {
  state.races = await listRaces();
  if (!state.races.some((race) => race.id === state.raceId)) {
    const primary = state.races.find((race) => race.id === "salomon-vitosha-27");
    state.raceId = (primary || state.races[0])?.id || "";
  }
}
