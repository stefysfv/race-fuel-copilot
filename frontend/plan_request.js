/** Form values that become an engine plan request. */

import { listRaces } from "./engine_client.js";
import { customFoodPayload, packedVestPayload, renderGutPill } from "./food_menu.js";
import { DEFAULT_TARGETS, state, ui } from "./state.js";
import { clampCarbs, paintMinutes } from "./ui_helpers.js";

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

export function readAthlete(pace) {
  const weight = Number(ui.weight.value);
  const temperature = Number(ui.temperature.value);
  if (!(weight > 30 && weight < 250)) {
    throw new Error("Weight must be between 30 and 250 kg.");
  }
  if (!(temperature >= -20 && temperature <= 45)) {
    throw new Error("Temperature must be between −20 and 45 °C.");
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
  if (state.customAids.length) {
    body.aid_stations = state.customAids.map((station) => {
      const row = {
        name: station.name,
        distance_km: station.distance_km,
        has_water: station.has_water !== false,
        has_food: Boolean(station.has_food),
      };
      if (station.cutoff_hours) {
        row.cutoff_hours = station.cutoff_hours;
      }
      return row;
    });
  }
  if (state.source === "gpx") {
    if (!state.gpxXml.trim()) {
      throw new Error("Choose a GPX file first.");
    }
    body.gpx_xml = state.gpxXml;
    return body;
  }
  if (!state.raceId) {
    throw new Error("Choose a race first.");
  }
  body.race_id = state.raceId;
  return body;
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
  const show = state.source === "gpx" || state.editStations;
  ui.aidDrawer?.classList.toggle("hidden", !show);
  if (show && ui.aidDrawer) ui.aidDrawer.open = true;
  const toggle = document.querySelector("#edit-stations");
  if (toggle) toggle.textContent = show && state.source !== "gpx" ? "Hide Stations" : "Edit Stations";
}

export function setSource(source) {
  state.source = source;
  document.querySelector("#source-race")?.setAttribute("aria-checked", source === "race" ? "true" : "false");
  document.querySelector("#source-gpx")?.setAttribute("aria-checked", source === "gpx" ? "true" : "false");
  if (ui.raceSelect) ui.raceSelect.disabled = source !== "race";
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
  ui.raceSelect.replaceChildren();
  for (const race of state.races) {
    const option = document.createElement("option");
    option.value = race.id;
    option.textContent = `${race.name} · ${race.distance_km} km`;
    ui.raceSelect.append(option);
  }
  if (!state.races.some((race) => race.id === state.raceId)) {
    const primary = state.races.find((race) => race.id === "salomon-vitosha-27");
    state.raceId = (primary || state.races[0])?.id || "";
  }
  ui.raceSelect.value = state.raceId;
}
