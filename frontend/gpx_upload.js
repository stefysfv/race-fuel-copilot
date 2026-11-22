/** Load a GPX on the setup screen, then show the sanity card.

Inspection runs in the fueling worker. Recent courses live in IndexedDB.
*/

import { inspectGpx } from "./engine_client.js";
import { loadRecentCourses, rememberCourse } from "./offline_db.js";
import { setSource } from "./plan_request.js";
import { state, ui } from "./state.js";
import {
  distanceFromDisplay,
  distanceToDisplay,
  distanceUnit,
  formatDistance,
  formatElevation,
} from "./units.js";

const NO_AUTOFILL = { autocomplete: "off", autocorrect: "off", autocapitalize: "off", spellcheck: "false" };

function noAutofill(input) {
  for (const [name, value] of Object.entries(NO_AUTOFILL)) input.setAttribute(name, value);
  return input;
}

const MAX_BYTES = 8_000_000;

function plainError(error) {
  const text = String(error?.message || error || "The GPX could not be read.");
  const matches = [...text.matchAll(/ValueError:\s*([^\n]+)/g)];
  return matches.length ? matches[matches.length - 1][1].trim() : text;
}

function showError(message) {
  state.gpxReady = false;
  state.gpxError = message;
  state.gpxReport = null;
  const node = document.querySelector("#gpx-error");
  if (node) {
    node.textContent = message;
    node.classList.remove("hidden");
  }
  document.querySelector("#sanity-card")?.classList.add("hidden");
}

function hideError() {
  state.gpxError = "";
  const node = document.querySelector("#gpx-error");
  if (node) {
    node.textContent = "";
    node.classList.add("hidden");
  }
}

function overrideKm() {
  const value = Number(document.querySelector("#distance-override")?.value);
  return value > 0 ? distanceFromDisplay(value) : null;
}

function distanceScale(report) {
  const override = overrideKm();
  if (!override || !report?.distance_km) return 1;
  return override / report.distance_km;
}

function drawProfile(points) {
  const canvas = document.querySelector("#sanity-profile");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  if (!points || points.length < 2) return;
  const eles = points.map((point) => Number(point.ele) || 0);
  const low = Math.min(...eles);
  const high = Math.max(...eles);
  const span = high - low || 1;
  ctx.beginPath();
  ctx.strokeStyle = "#ff5500";
  ctx.lineWidth = 3;
  ctx.lineJoin = "round";
  points.forEach((point, index) => {
    const x = 8 + (index / (points.length - 1)) * (width - 16);
    const y = height - 10 - ((Number(point.ele) - low) / span) * (height - 24);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function syncWaypointsFromDom() {
  const rows = [];
  for (const item of document.querySelectorAll("#waypoint-list [data-role='waypoint']")) {
    const type = item.querySelector("[data-role='type']")?.value || "water";
    rows.push({
      checked: Boolean(item.querySelector("[data-role='check']")?.checked),
      name: item.querySelector("[data-role='name']")?.value.trim() || "",
      distance_km: distanceFromDisplay(Number(item.querySelector("[data-role='km']")?.value)),
      base_km: Number(item.dataset.baseKm),
      has_food: type === "food",
      has_water: true,
      drop_bag: Boolean(item.querySelector("[data-role='drop']")?.checked),
      dirty: item.dataset.dirty === "1",
    });
  }
  state.waypointAids = rows;
}

function paintScaleNote(report) {
  const note = document.querySelector("#sanity-scale");
  const override = overrideKm();
  state.distanceOverrideKm = override;
  if (!note) return;
  note.textContent = override
    ? `The plan will use ${formatDistance(override, { digits: 1 })}.`
    : "";
  const scale = distanceScale(report);
  for (const item of document.querySelectorAll("#waypoint-list [data-role='waypoint']")) {
    if (item.dataset.dirty === "1") continue;
    const km = item.querySelector("[data-role='km']");
    const base = Number(item.dataset.baseKm);
    if (km && Number.isFinite(base)) km.value = distanceToDisplay(base * scale).toFixed(2);
  }
  syncWaypointsFromDom();
}

function paintWaypoints(report) {
  const block = document.querySelector("#waypoint-block");
  const list = document.querySelector("#waypoint-list");
  if (!block || !list) return;
  const stations = report.waypoints || [];
  block.classList.toggle("hidden", stations.length === 0);
  list.replaceChildren();
  const scale = distanceScale(report);
  for (const station of stations) {
    const item = document.createElement("li");
    item.dataset.role = "waypoint";
    item.dataset.baseKm = String(station.distance_km);
    item.className = "waypoint-row";
    const check = document.createElement("input");
    check.type = "checkbox";
    check.checked = true;
    check.dataset.role = "check";
    check.setAttribute("aria-label", `Use ${station.name}`);
    const name = document.createElement("input");
    name.type = "text";
    name.maxLength = 80;
    name.value = station.name;
    name.dataset.role = "name";
    name.setAttribute("aria-label", "Aid station name");
    noAutofill(name);
    const km = document.createElement("input");
    km.type = "number";
    km.min = "0";
    km.max = "500";
    km.step = "0.01";
    km.value = distanceToDisplay(Number(station.distance_km) * scale).toFixed(2);
    km.dataset.role = "km";
    km.setAttribute("aria-label", `Aid station distance (${distanceUnit()})`);
    noAutofill(km);
    const type = document.createElement("select");
    type.dataset.role = "type";
    type.setAttribute("aria-label", "Aid station type");
    for (const [value, label] of [["food", "Food and water"], ["water", "Water only"]]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      type.append(option);
    }
    type.value = station.has_food ? "food" : "water";
    const dropLabel = document.createElement("label");
    dropLabel.className = "check";
    const drop = document.createElement("input");
    drop.type = "checkbox";
    drop.checked = Boolean(station.drop_bag);
    drop.dataset.role = "drop";
    dropLabel.append(drop, document.createTextNode(" Drop bag"));
    item.append(check, name, km, type, dropLabel);
    item.addEventListener("input", (event) => {
      if (event.target === km) item.dataset.dirty = "1";
      syncWaypointsFromDom();
    });
    list.append(item);
  }
  syncWaypointsFromDom();
}

function paintTracks(report) {
  const field = document.querySelector("#track-field");
  const select = document.querySelector("#gpx-track");
  if (!field || !select) return;
  const tracks = report.tracks || [];
  const show = tracks.length > 1;
  field.classList.toggle("hidden", !show);
  select.replaceChildren();
  if (!show) return;
  for (const track of tracks) {
    const option = document.createElement("option");
    option.value = String(track.index);
    const kind = track.kind === "route" ? "Route" : "Track";
    option.textContent = `${track.name} · ${kind} · ${track.points} points`;
    select.append(option);
  }
  const merge = document.createElement("option");
  merge.value = "merge";
  merge.textContent = "Merge in file order";
  select.append(merge);
  state.gpxTrack = String(report.selected);
  select.value = state.gpxTrack;
}

export function paintGpxReport(report) {
  if (!report) return;
  state.gpxReport = report;
  state.gpxReady = true;
  hideError();
  const card = document.querySelector("#sanity-card");
  card?.classList.remove("hidden");
  const distance = document.querySelector("#sanity-distance");
  const gain = document.querySelector("#sanity-gain");
  const loss = document.querySelector("#sanity-loss");
  const high = document.querySelector("#sanity-high");
  if (distance) distance.textContent = formatDistance(report.distance_km, { digits: 2 });
  if (gain) gain.textContent = formatElevation(report.elevation_gain_m);
  if (loss) loss.textContent = formatElevation(report.elevation_loss_m);
  if (high) high.textContent = formatElevation(report.highest_m);
  drawProfile(report.profile || []);
  const warnings = document.querySelector("#sanity-warnings");
  if (warnings) {
    warnings.replaceChildren();
    for (const note of report.warnings || []) {
      const item = document.createElement("li");
      item.textContent = note;
      warnings.append(item);
    }
  }
  paintTracks(report);
  paintWaypoints(report);
  paintScaleNote(report);
}

function paintRecent(courses) {
  const host = document.querySelector("#recent-courses");
  if (!host) return;
  host.replaceChildren();
  if (!courses.length) return;
  const label = document.createElement("p");
  label.className = "kicker";
  label.textContent = "Recent courses";
  host.append(label);
  const row = document.createElement("div");
  row.className = "recent-row";
  for (const course of courses) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-btn liquid-pill recent-chip";
    button.dataset.testid = "recent-course";
    button.textContent = course.name || "GPX";
    button.addEventListener("click", () => openStored(course));
    row.append(button);
  }
  host.append(row);
}

async function inspectCurrent() {
  hideError();
  try {
    const report = await inspectGpx(state.gpxXml, state.gpxTrack || "longest");
    paintGpxReport(report);
    if (ui.gpxName) ui.gpxName.textContent = state.gpxName ? `Loaded ${state.gpxName}` : "Loaded GPX";
  } catch (error) {
    showError(plainError(error));
    if (ui.gpxName) ui.gpxName.textContent = state.gpxName ? state.gpxName : "";
  }
}

async function openStored(course) {
  if (!course?.gpxXml) return;
  state.gpxXml = course.gpxXml;
  state.gpxName = course.name || "GPX";
  state.gpxTrack = "longest";
  const override = document.querySelector("#distance-override");
  if (override) override.value = "";
  state.distanceOverrideKm = null;
  setSource("gpx");
  if (ui.gpxName) ui.gpxName.textContent = `Reading ${state.gpxName}`;
  await inspectCurrent();
}

async function acceptFile(file) {
  if (!file) return;
  if (file.size > MAX_BYTES) {
    state.gpxName = file.name;
    if (ui.gpxName) ui.gpxName.textContent = file.name;
    showError("This file is too large. Keep it under 8 MB.");
    return;
  }
  let xml = "";
  try {
    xml = await file.text();
  } catch (error) {
    showError("This file could not be read.");
    return;
  }
  if (xml.length > MAX_BYTES) {
    showError("This file is too large. Keep it under 8 MB.");
    return;
  }
  state.gpxXml = xml;
  state.gpxName = file.name;
  state.gpxTrack = "longest";
  const override = document.querySelector("#distance-override");
  if (override) override.value = "";
  state.distanceOverrideKm = null;
  setSource("gpx");
  if (ui.gpxName) ui.gpxName.textContent = `Reading ${file.name}`;
  await inspectCurrent();
  if (!state.gpxReady) return;
  const saved = await rememberCourse({
    id: `${file.name}:${xml.length}`,
    name: file.name,
    savedAt: Date.now(),
    gpxXml: xml,
  });
  if (saved) paintRecent(saved);
}

export function checkedWaypointAids() {
  syncWaypointsFromDom();
  return state.waypointAids
    .filter((station) => station.checked && station.name && Number.isFinite(station.distance_km))
    .map((station) => ({
      name: station.name.slice(0, 80),
      distance_km: station.distance_km,
      has_water: station.has_water !== false,
      has_food: Boolean(station.has_food),
      drop_bag: Boolean(station.drop_bag),
    }));
}

export function repaintGpxUnits() {
  const overrideInput = document.querySelector("#distance-override");
  if (overrideInput && state.distanceOverrideKm > 0) {
    overrideInput.value = String(Math.round(distanceToDisplay(state.distanceOverrideKm) * 100) / 100);
  }
  if (state.gpxReport) {
    const prevAids = state.waypointAids.slice();
    paintGpxReport(state.gpxReport);
    if (prevAids.length) {
      const items = [...document.querySelectorAll("#waypoint-list [data-role='waypoint']")];
      prevAids.forEach((saved, idx) => {
        const item = items[idx];
        if (!item) return;
        const check = item.querySelector("[data-role='check']");
        const name = item.querySelector("[data-role='name']");
        const km = item.querySelector("[data-role='km']");
        const type = item.querySelector("[data-role='type']");
        const drop = item.querySelector("[data-role='drop']");
        if (check) check.checked = Boolean(saved.checked);
        if (name && saved.name) name.value = saved.name;
        if (km && Number.isFinite(saved.distance_km)) km.value = distanceToDisplay(saved.distance_km).toFixed(2);
        if (type) type.value = saved.has_food ? "food" : "water";
        if (drop) drop.checked = Boolean(saved.drop_bag);
        if (saved.dirty) item.dataset.dirty = "1";
      });
      syncWaypointsFromDom();
    }
  }
}

export function setupGpxUpload() {
  const input = document.querySelector("#gpx-file");
  const drop = document.querySelector("#gpx-drop");
  document.querySelector("#source-gpx")?.addEventListener("click", () => input?.click());
  input?.addEventListener("change", () => {
    const file = input.files?.[0];
    if (file) acceptFile(file);
    input.value = "";
  });
  drop?.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("is-over");
  });
  drop?.addEventListener("dragleave", () => drop.classList.remove("is-over"));
  drop?.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("is-over");
    const file = event.dataTransfer?.files?.[0];
    if (file) acceptFile(file);
  });
  document.querySelector("#gpx-track")?.addEventListener("change", (event) => {
    const next = event.target.value || "longest";
    if (next === state.gpxTrack) return;
    state.gpxTrack = next;
    if (state.gpxXml) inspectCurrent();
  });
  document.querySelector("#distance-override")?.addEventListener("input", () => {
    if (state.gpxReport) paintScaleNote(state.gpxReport);
  });
  document.querySelector("#use-example")?.addEventListener("click", () => {
    setSource("race");
  });
  if (state.gpxReport) paintGpxReport(state.gpxReport);
  loadRecentCourses().then(paintRecent).catch(() => paintRecent([]));
}
