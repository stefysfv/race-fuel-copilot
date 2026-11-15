/** UI controller.

The sports math stays on the server. This file collects the form, calls
`/api/races`, `/api/generate-plan`, and `/api/recalibrate`, and paints the
three tabs. A target finish is not an engine input: the flat GAP pace is
solved with two plan requests so the predicted clock matches the hours the
runner typed.
*/

import { loadSession, saveSession } from "./offline_db.js";
import { segmentAtMinute, snapToSegments } from "./offline_engine.js";
import { createMapView, positionOnCourse } from "./map_controller.js";

const PRESETS = [
  { name: "Standard Gel", carbs_g: 25, caffeine_mg: 0, kind: "gel" },
  { name: "Isotonic Gel", carbs_g: 22, caffeine_mg: 0, kind: "gel" },
  { name: "High-Carb Gel", carbs_g: 40, caffeine_mg: 0, kind: "gel" },
  { name: "Drink Mix", carbs_g: 30, caffeine_mg: 0, kind: "liquid" },
  { name: "Energy Bar", carbs_g: 35, caffeine_mg: 0, kind: "bar" },
  { name: "Caffeinated Gel", carbs_g: 25, caffeine_mg: 100, kind: "gel" },
];

const DEFAULT_TARGETS = {
  "salomon-vitosha-12": { hours: 2, minutes: 0 },
  "salomon-vitosha-27": { hours: 4, minutes: 30 },
  "salomon-vitosha-40": { hours: 6, minutes: 30 },
  "vitosha-100": { hours: 15, minutes: 45 },
};

const state = {
  races: [],
  source: "race",
  raceId: "salomon-vitosha-27",
  gpxXml: "",
  gpxName: "",
  carbs: 60,
  aidStations: [],
  packedPlan: null,
  livePlan: null,
  logged: [],
  startedAt: null,
  giRescue: false,
  snap: null,
  targetEdited: false,
  locker: [],
  fuelPreference: "gels_first",
  sim: { active: false, km: 0, drift: 1, elapsed: 0, playing: false, frame: 0, busy: false },
};

const ui = {
  status: document.querySelector("#app-status"),
  raceSelect: document.querySelector("#race-select"),
  gpxFile: document.querySelector("#gpx-file"),
  gpxName: document.querySelector("#gpx-name"),
  weight: document.querySelector("#weight"),
  temperature: document.querySelector("#temperature"),
  hours: document.querySelector("#target-hours"),
  minutes: document.querySelector("#target-minutes"),
  summary: document.querySelector("#plan-summary"),
  warnings: document.querySelector("#warnings"),
  packing: document.querySelector("#packing-list"),
  mapEmpty: document.querySelector("#map-empty"),
  mapBlock: document.querySelector("#map-block"),
  countdown: document.querySelector("#countdown"),
  caption: document.querySelector("#clock-caption"),
  intake: document.querySelector("#intake-detail"),
  badge: document.querySelector("#zone-badge"),
  grade: document.querySelector("#grade-readout"),
  nextAid: document.querySelector("#next-aid"),
  liveMessage: document.querySelector("#live-message"),
  caffeine: document.querySelector("#caffeine-readout"),
  log: document.querySelector("#log-intake"),
  gps: document.querySelector("#update-gps"),
  rescue: document.querySelector("#gi-rescue"),
  build: document.querySelector("#build-plan"),
  exportCard: document.querySelector("#export-card"),
  raceCard: document.querySelector("#race-card"),
};

let mapView = null;
let clockTimer = null;

function mapApi() {
  if (!mapView) {
    if (!window.L || !window.Chart) {
      throw new Error("The map libraries did not load. Reload while you have a connection.");
    }
    mapView = createMapView(document.querySelector("#map"), document.querySelector("#elevation-chart"));
  }
  return mapView;
}

function setStatus(message) {
  ui.status.textContent = message;
}

function formatClock(minutes) {
  const sign = minutes < 0 ? "−" : "";
  const total = Math.abs(Math.round(minutes * 60));
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const body = `${mins}:${String(secs).padStart(2, "0")}`;
  return hours > 0 ? `${sign}${hours}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${sign}${body}`;
}

function formatDuration(minutes) {
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours > 0 ? `${hours} h ${mins} min` : `${mins} min`;
}

function elapsedMin() {
  if (state.sim.active) {
    return state.sim.elapsed;
  }
  if (!state.startedAt) {
    return 0;
  }
  return (Date.now() - state.startedAt) / 60000;
}

function readTargetMin() {
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

function readAthlete(pace) {
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
    fuel_preference: state.fuelPreference,
  };
  if (state.locker.length) {
    athlete.locker = state.locker.map((item) => ({
      name: item.name,
      carbs_g: item.carbs_g,
      caffeine_mg: item.caffeine_mg,
      kind: item.kind,
    }));
  }
  return athlete;
}

function courseBody(athlete) {
  if (state.source === "gpx") {
    if (!state.gpxXml.trim()) {
      throw new Error("Choose a GPX file first.");
    }
    return { gpx_xml: state.gpxXml, athlete };
  }
  if (!state.raceId) {
    throw new Error("Choose a race first.");
  }
  return { race_id: state.raceId, athlete };
}

async function postJson(path, body) {
  let response;
  try {
    response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (error) {
    throw new Error("No connection to the fueling server.");
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = payload.detail;
    const message = typeof detail === "string" ? detail : "The server rejected that request.";
    throw new Error(message);
  }
  return payload;
}

function selectedRace() {
  return state.races.find((race) => race.id === state.raceId) || null;
}

function applyTargetDefault() {
  if (state.targetEdited) {
    return;
  }
  const preset = DEFAULT_TARGETS[state.raceId] || { hours: 6, minutes: 0 };
  ui.hours.value = String(preset.hours);
  ui.minutes.value = String(preset.minutes);
}

function setSource(source) {
  state.source = source;
  document.querySelector("#source-race").setAttribute("aria-checked", source === "race" ? "true" : "false");
  document.querySelector("#source-gpx").setAttribute("aria-checked", source === "gpx" ? "true" : "false");
  ui.raceSelect.disabled = source !== "race";
}

function setCarbs(grams) {
  state.carbs = grams;
  for (const button of document.querySelectorAll(".gut")) {
    button.setAttribute("aria-checked", Number(button.dataset.carbs) === grams ? "true" : "false");
  }
}

async function loadRaces() {
  const response = await fetch("/api/races");
  if (!response.ok) {
    throw new Error("Could not load the race list.");
  }
  const payload = await response.json();
  state.races = payload.races;
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

async function buildPlan(event) {
  event.preventDefault();
  ui.build.disabled = true;
  try {
    const targetMin = readTargetMin();
    setStatus("Building the plan…");
    const probe = await postJson("/api/generate-plan", courseBody(readAthlete(8)));
    let pace = 8 * (targetMin / probe.predicted_duration_min);
    pace = Math.min(19.9, Math.max(3.51, pace));
    const plan = await postJson("/api/generate-plan", courseBody(readAthlete(Number(pace.toFixed(3)))));
    state.packedPlan = plan;
    state.livePlan = plan;
    state.logged = [];
    state.startedAt = null;
    state.giRescue = false;
    state.snap = null;
    resetSim();
    state.aidStations = state.source === "race" ? (selectedRace()?.aid_stations || []) : [];
    const missed = Math.abs(plan.predicted_duration_min - targetMin) > 1;
    setStatus(missed
      ? `Plan ready. The closest effort pace the engine allows finishes in ${formatDuration(plan.predicted_duration_min)}.`
      : "Plan ready. Packing splits are below.");
    renderPacking();
    renderLive();
    drawMap();
    await persist();
  } catch (error) {
    setStatus(error.message);
  } finally {
    ui.build.disabled = false;
  }
}

function renderPacking() {
  const plan = state.packedPlan;
  ui.packing.replaceChildren();
  ui.warnings.replaceChildren();
  if (!plan) {
    ui.summary.classList.add("hidden");
    ui.exportCard.disabled = true;
    ui.raceCard.classList.add("hidden");
    return;
  }
  const athlete = plan.athlete;
  ui.summary.classList.remove("hidden");
  ui.summary.innerHTML = `
    <p class="text-lg font-extrabold">${plan.race_name}</p>
    <p class="mt-1 text-sm leading-relaxed text-slate-200">
      Predicted ${formatDuration(plan.predicted_duration_min)}
      at ${athlete.flat_pace_min_per_km.toFixed(2)} min/km flat effort.
      Sweat ${Math.round(athlete.fluid_ml_per_hour)} ml/h ·
      sodium ${Math.round(athlete.sodium_mg_per_hour)} mg/h ·
      carbs ${Math.round(athlete.carbs_g_per_hour)} g/h.
      Caffeine ${Math.round(plan.totals?.caffeine_mg || 0)} / ${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg.
    </p>`;
  for (const warning of plan.warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    ui.warnings.append(item);
  }
  for (const leg of plan.aid_legs) {
    const station = state.aidStations.find((item) => item.name === leg.to_name);
    const card = document.createElement("article");
    card.className = "panel";
    const tags = station
      ? [station.has_water ? "Water" : "", station.has_food ? "Food" : "", station.drop_bag ? "Drop bag" : ""].filter(Boolean)
      : [];
    card.innerHTML = `
      <h2 class="text-lg font-extrabold leading-tight">${leg.from_name} → ${leg.to_name}</h2>
      <p class="mt-1 text-sm text-slate-300">${leg.from_km.toFixed(1)} km → ${leg.to_km.toFixed(1)} km · ${formatDuration(leg.predicted_minutes)}</p>
      <p class="mt-3 text-base font-semibold">Vest carry · ${Math.round(leg.vest_carbs_g ?? leg.carbs_g)} g carbs · ${Math.round(leg.fluid_ml)} ml · ${Math.round(leg.sodium_mg)} mg sodium</p>
      <ul class="mt-2 space-y-1 text-sm text-slate-100"></ul>
      <p class="eat-note mt-2 text-sm font-semibold text-slate-100"></p>
      <p class="station-note mt-2 text-sm text-slate-300"></p>`;
    const list = card.querySelector("ul");
    for (const item of leg.pack) {
      const row = document.createElement("li");
      row.textContent = `${item.quantity} × ${item.item} — ${item.detail}`;
      list.append(row);
    }
    card.querySelector(".eat-note").textContent = leg.station_meal
      ? `Eat at station · ${Math.round(leg.station_carbs_g)} g · ${leg.station_meal}`
      : "Eat at station · nothing plated on this arrival.";
    const note = card.querySelector(".station-note");
    note.textContent = [tags.join(" · "), station?.notes || ""].filter(Boolean).join(" — ");
    ui.packing.append(card);
  }
  ui.exportCard.disabled = false;
}

function nextStop() {
  const plan = state.livePlan;
  if (!plan) {
    return null;
  }
  if (state.sim.active) {
    return plan.fuel_stops.find((stop) => stop.scheduled_minute > state.sim.elapsed + 0.05) || null;
  }
  return plan.fuel_stops.find((stop) => !state.logged.includes(stop.sequence)) || null;
}

function currentSegment() {
  const segments = state.packedPlan?.segments || [];
  if (state.sim.active) {
    return positionOnCourse(segments, state.sim.km * 1000)?.segment || null;
  }
  if (state.snap) {
    return segments.find((segment) => state.snap.distanceM >= segment.start_distance_m && state.snap.distanceM < segment.end_distance_m)
      || segments[segments.length - 1]
      || null;
  }
  return segmentAtMinute(segments, elapsedMin());
}

function nextAid(elapsed) {
  const plan = state.livePlan;
  if (!plan?.aid_legs.length) {
    return null;
  }
  let cursor = plan.clock_start_min;
  for (const leg of plan.aid_legs) {
    const end = cursor + leg.predicted_minutes;
    if (end > elapsed + 0.05 && leg.to_name !== "Finish") {
      return { name: leg.to_name, km: leg.to_km, etaMin: Math.max(0, end - elapsed) };
    }
    if (end > elapsed + 0.05) {
      return { name: "Finish", km: leg.to_km, etaMin: Math.max(0, end - elapsed) };
    }
    cursor = end;
  }
  return null;
}

function renderLive() {
  const hasPlan = Boolean(state.livePlan);
  ui.log.disabled = !hasPlan;
  ui.gps.disabled = !hasPlan;
  ui.rescue.disabled = !hasPlan;
  const stop = nextStop();
  const elapsed = elapsedMin();
  const clockOn = Boolean(state.startedAt) || state.sim.active;
  paintCaffeine(elapsed);
  paintDesk();
  if (!hasPlan) {
    ui.countdown.textContent = "--:--";
    ui.caption.textContent = "Build a plan, then tap the timer to start the race clock.";
    ui.intake.textContent = "";
    paintBadge(null);
    ui.nextAid.textContent = "Next aid station appears after a plan is built.";
    return;
  }
  if (!clockOn) {
    ui.countdown.textContent = stop ? formatClock(stop.scheduled_minute) : "Done";
    ui.caption.textContent = stop
      ? "Tap the timer to start the race clock. The number is the first intake time."
      : "No intake on this plan.";
  } else if (!stop) {
    ui.countdown.textContent = "Done";
    ui.caption.textContent = "Every scheduled intake is logged.";
  } else {
    const remaining = stop.scheduled_minute - elapsed;
    ui.countdown.textContent = remaining > 0 ? formatClock(remaining) : "Due";
    ui.caption.textContent = remaining > 0 ? "Until the next intake" : "This intake is due now";
  }
  ui.intake.textContent = stop
    ? `${stop.distance_km.toFixed(1)} km · ${stop.grade_percent >= 0 ? "+" : ""}${stop.grade_percent.toFixed(1)}% · ${stop.reason}`
    : "";
  paintBadge(currentSegment());
  const aid = nextAid(clockOn ? elapsed : 0);
  ui.nextAid.textContent = aid
    ? `Next: ${aid.name} · ${aid.km.toFixed(1)} km · ETA ${formatDuration(aid.etaMin)}`
    : "No aid station left on the course.";
}

function paintBadge(segment) {
  ui.badge.className = "zone-badge";
  if (!segment) {
    ui.badge.classList.add("idle");
    ui.badge.textContent = "No plan";
    ui.grade.textContent = "—";
    return;
  }
  ui.badge.classList.add(segment.zone);
  ui.badge.textContent = segment.zone.charAt(0).toUpperCase() + segment.zone.slice(1);
  const sign = segment.grade_percent >= 0 ? "+" : "";
  const km = state.snap ? state.snap.distanceM / 1000 : segment.start_distance_m / 1000;
  ui.grade.textContent = `${sign}${segment.grade_percent.toFixed(1)}% · ${km.toFixed(1)} km`;
}

function drawMap() {
  if (!state.packedPlan) {
    ui.mapEmpty.classList.remove("hidden");
    ui.mapBlock.classList.add("hidden");
    return;
  }
  ui.mapEmpty.classList.add("hidden");
  ui.mapBlock.classList.remove("hidden");
  try {
    const view = mapApi();
    view.setCourse({
      segments: state.packedPlan.segments,
      aidStations: state.aidStations,
      fuelStops: (state.livePlan || state.packedPlan).fuel_stops,
    });
    if (state.snap) {
      view.setRunner(state.snap.latitude, state.snap.longitude);
    }
  } catch (error) {
    setStatus(error.message);
  }
}

function showTab(name) {
  const tabs = { setup: "tab-setup", map: "tab-map", live: "tab-live" };
  for (const [key, id] of Object.entries(tabs)) {
    document.querySelector(`#${id}`).classList.toggle("hidden", key !== name);
    const tabButton = document.querySelector(`#nav-${key}`);
    if (key === name) {
      tabButton.setAttribute("aria-current", "page");
    } else {
      tabButton.removeAttribute("aria-current");
    }
  }
  if (name === "map") {
    drawMap();
    requestAnimationFrame(() => mapView?.resize());
  }
  if (name === "live") {
    renderLive();
  }
}

function startClock() {
  if (!state.packedPlan || state.startedAt) {
    return;
  }
  state.startedAt = Date.now();
  ensureTicker();
  renderLive();
  persist();
}

function ensureTicker() {
  if (clockTimer || !state.startedAt) {
    return;
  }
  clockTimer = setInterval(() => {
    if (document.querySelector("#tab-live").classList.contains("hidden")) {
      return;
    }
    renderLive();
  }, 1000);
}

async function logIntake() {
  const stop = nextStop();
  if (!stop) {
    setStatus("No intake left to log.");
    return;
  }
  if (!state.startedAt) {
    startClock();
  }
  state.logged.push(stop.sequence);
  setStatus(`Logged ${stop.carbs_g.toFixed(0)} g at ${stop.distance_km.toFixed(1)} km.`);
  renderLive();
  await persist();
}

function readGps() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("This browser has no GPS."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve(position.coords),
      () => reject(new Error("GPS permission was denied or the fix timed out.")),
      { enableHighAccuracy: true, timeout: 12000, maximumAge: 5000 },
    );
  });
}

async function updateGps() {
  if (!state.startedAt) {
    startClock();
  }
  ui.gps.disabled = true;
  try {
    const coords = await readGps();
    await sendFix(coords.latitude, coords.longitude, false);
  } catch (error) {
    if (error.message === "No connection to the fueling server." && state.packedPlan) {
      setStatus("No connection. The marker will move when a GPS fix is available, and the saved plan stays as it is.");
    } else {
      setStatus(error.message);
    }
  } finally {
    ui.gps.disabled = false;
  }
}

async function triggerRescue() {
  if (state.sim.active && state.snap) {
    ui.rescue.disabled = true;
    try {
      await sendFix(state.snap.latitude, state.snap.longitude, true);
    } catch (error) {
      setStatus(error.message);
    } finally {
      ui.rescue.disabled = !state.livePlan;
    }
    return;
  }
  if (!state.startedAt) {
    startClock();
  }
  ui.rescue.disabled = true;
  try {
    let latitude;
    let longitude;
    try {
      const coords = await readGps();
      latitude = coords.latitude;
      longitude = coords.longitude;
    } catch (error) {
      const segment = currentSegment();
      if (!segment) {
        throw error;
      }
      latitude = segment.start_lat;
      longitude = segment.start_lon;
      await sendFix(latitude, longitude, true);
      ui.liveMessage.textContent = `No GPS fix, so this used your position on the clock. ${ui.liveMessage.textContent}`;
      return;
    }
    await sendFix(latitude, longitude, true);
  } catch (error) {
    setStatus(error.message);
  } finally {
    ui.rescue.disabled = !state.livePlan;
  }
}

async function sendFix(latitude, longitude, giRescue) {
  const athlete = state.livePlan.athlete;
  const body = {
    ...courseBody(athlete),
    latitude,
    longitude,
    elapsed_min: Number(elapsedMin().toFixed(3)),
    gi_rescue: giRescue,
  };
  try {
    const result = await postJson("/api/recalibrate", body);
    state.livePlan = result.remaining;
    state.logged = [];
    state.giRescue = result.gi_rescue_applied;
    state.snap = {
      latitude: result.snap.latitude,
      longitude: result.snap.longitude,
      distanceM: result.snap.distance_along_m,
      offCourseM: result.snap.off_course_m,
      onCourse: result.snap.on_course,
    };
    if (state.sim.active) {
      state.sim.km = result.snap.distance_along_m / 1000;
    }
    ui.liveMessage.textContent = result.message;
    setStatus(result.message);
  } catch (error) {
    if (error.message !== "No connection to the fueling server.") {
      throw error;
    }
    const snapped = snapToSegments(state.packedPlan.segments, latitude, longitude);
    state.snap = snapped;
    ui.liveMessage.textContent = snapped.onCourse
      ? "Offline. Snapped to the saved course. The fuel plan was not rebuilt."
      : `Offline and ${Math.round(snapped.offCourseM)} m off the saved course. The fuel plan was not rebuilt.`;
    setStatus(ui.liveMessage.textContent);
  }
  drawMap();
  renderLive();
  await persist();
}

async function persist() {
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
      packedPlan: state.packedPlan,
      livePlan: state.livePlan,
      locker: state.locker,
      fuelPreference: state.fuelPreference,
      logged: state.logged,
      startedAt: state.startedAt,
      giRescue: state.giRescue,
      snap: state.snap,
    });
  } catch (error) {
    setStatus("The plan is on screen, but it could not be saved on this phone.");
  }
}

function restore(session) {
  if (!session) {
    return;
  }
  state.source = session.source || "race";
  state.raceId = session.raceId || state.raceId;
  state.gpxXml = session.gpxXml || "";
  state.gpxName = session.gpxName || "";
  state.carbs = session.carbs || 60;
  state.targetEdited = Boolean(session.targetEdited);
  state.aidStations = session.aidStations || [];
  state.packedPlan = session.packedPlan;
  state.livePlan = session.livePlan;
  state.logged = session.logged || [];
  state.locker = Array.isArray(session.locker) ? session.locker : [];
  state.fuelPreference = session.fuelPreference || "gels_first";
  setFuelPreference(state.fuelPreference);
  state.startedAt = session.startedAt;
  state.giRescue = Boolean(session.giRescue);
  state.snap = session.snap;
  ui.weight.value = session.weight ?? "70";
  ui.temperature.value = session.temperature ?? "18";
  ui.hours.value = session.hours ?? "5";
  ui.minutes.value = session.minutes ?? "30";
  ui.gpxName.textContent = state.gpxName ? `Loaded ${state.gpxName}` : "";
  setSource(state.source);
  setCarbs(state.carbs);
  if (state.giRescue) {
    ui.liveMessage.textContent = "GI Rescue is on for the rest of this plan.";
  }
}

function driftLabel(drift) {
  if (drift === 0.9) return "Fast -10%";
  if (drift === 1.15) return "Slow +15%";
  if (drift === 1.3) return "Bonking +30%";
  return "On Target";
}

function resetSim() {
  if (state.sim.frame) {
    cancelAnimationFrame(state.sim.frame);
  }
  state.sim.active = false;
  state.sim.km = 0;
  state.sim.drift = 1;
  state.sim.elapsed = 0;
  state.sim.playing = false;
  state.sim.frame = 0;
  state.sim.busy = false;
}

function minuteAtKm(segments, km) {
  const point = positionOnCourse(segments, km * 1000);
  if (!point) {
    return 0;
  }
  const segment = point.segment;
  const span = segment.end_distance_m - segment.start_distance_m;
  const fraction = span <= 0 ? 0 : (point.distanceM - segment.start_distance_m) / span;
  return segment.start_minute + fraction * segment.predicted_minutes;
}

function kmAtMinute(segments, minute) {
  const last = segments[segments.length - 1];
  if (minute <= segments[0].start_minute) {
    return segments[0].start_distance_m / 1000;
  }
  for (const segment of segments) {
    if (minute <= segment.end_minute) {
      const span = segment.predicted_minutes;
      const fraction = span <= 0 ? 0 : (minute - segment.start_minute) / span;
      return (segment.start_distance_m + Math.min(1, Math.max(0, fraction)) * segment.distance_m) / 1000;
    }
  }
  return last.end_distance_m / 1000;
}

function paintDesk() {
  const plan = state.packedPlan;
  for (const root of document.querySelectorAll("[data-desk]")) {
    root.classList.toggle("hidden", !plan);
    const range = root.querySelector("[data-desk-range]");
    if (range && plan) {
      range.max = String(plan.distance_km);
      range.value = String(state.sim.km);
      range.disabled = state.sim.busy;
    }
    const km = root.querySelector("[data-desk-km]");
    if (km) {
      km.textContent = `${state.sim.km.toFixed(1)} km`;
    }
    const elapsed = root.querySelector("[data-desk-elapsed]");
    if (elapsed) {
      elapsed.textContent = `Elapsed ${formatDuration(state.sim.elapsed)} · ${driftLabel(state.sim.drift)}`;
    }
    const play = root.querySelector("[data-desk-play]");
    if (play) {
      play.textContent = state.sim.playing ? "Pause" : "Play 60x";
      play.disabled = state.sim.busy || !plan;
    }
    for (const button of root.querySelectorAll("[data-drift]")) {
      const selected = Number(button.dataset.drift) === state.sim.drift;
      if (selected) {
        button.setAttribute("aria-checked", "true");
      } else {
        button.removeAttribute("aria-checked");
      }
      button.disabled = state.sim.busy || !plan;
    }
  }
}

function paintCaffeine(elapsed) {
  const plan = state.packedPlan;
  if (!plan?.totals) {
    ui.caffeine.textContent = "";
    return;
  }
  const used = (state.livePlan?.fuel_stops || [])
    .filter((stop) => (stop.caffeine_mg || 0) > 0 && stop.scheduled_minute <= elapsed + 0.05)
    .reduce((sum, stop) => sum + stop.caffeine_mg, 0);
  const planned = plan.totals.caffeine_mg || 0;
  const cap = plan.totals.caffeine_cap_mg || 0;
  ui.caffeine.textContent = `Caffeine so far ${Math.round(used)} mg · planned ${Math.round(planned)} / ${Math.round(cap)} mg`;
}

function moveRunner() {
  if (!mapView || !state.snap) {
    return;
  }
  mapView.setRunner(state.snap.latitude, state.snap.longitude);
}

function placeSim(km) {
  const segments = state.packedPlan.segments;
  const total = state.packedPlan.distance_km;
  state.sim.km = Math.min(total, Math.max(0, km));
  state.sim.elapsed = minuteAtKm(segments, state.sim.km) * state.sim.drift;
  state.sim.active = true;
  const point = positionOnCourse(segments, state.sim.km * 1000);
  state.snap = {
    latitude: point.latitude,
    longitude: point.longitude,
    distanceM: point.distanceM,
    offCourseM: 0,
    onCourse: true,
  };
  moveRunner();
  renderLive();
  return point;
}

let deskScrub = 0;

function onDeskRange(event) {
  if (!state.packedPlan || state.sim.busy) {
    return;
  }
  if (state.sim.playing) {
    stopPlay(false);
  }
  placeSim(Number(event.target.value));
  if (state.sim.drift === 1) {
    return;
  }
  window.clearTimeout(deskScrub);
  deskScrub = window.setTimeout(() => {
    if (!state.snap) {
      return;
    }
    sendFix(state.snap.latitude, state.snap.longitude, state.giRescue).catch((error) => setStatus(error.message));
  }, 400);
}

async function applyDrift(factor) {
  if (!state.packedPlan || state.sim.busy || !Number.isFinite(factor)) {
    return;
  }
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.drift = factor;
  const point = placeSim(state.sim.km);
  state.sim.busy = true;
  paintDesk();
  try {
    await sendFix(point.latitude, point.longitude, state.giRescue);
  } catch (error) {
    setStatus(error.message);
  } finally {
    state.sim.busy = false;
    paintDesk();
  }
}

function stopPlay(recalibrate) {
  state.sim.playing = false;
  if (state.sim.frame) {
    cancelAnimationFrame(state.sim.frame);
    state.sim.frame = 0;
  }
  paintDesk();
  if (recalibrate && state.sim.drift !== 1 && state.snap) {
    sendFix(state.snap.latitude, state.snap.longitude, state.giRescue).catch((error) => setStatus(error.message));
  }
}

function togglePlay() {
  if (!state.packedPlan || state.sim.busy) {
    return;
  }
  if (state.sim.playing) {
    stopPlay(true);
    return;
  }
  state.sim.playing = true;
  placeSim(state.sim.km);
  let last = performance.now();
  const tick = (now) => {
    if (!state.sim.playing || !state.packedPlan) {
      return;
    }
    const raceMinutes = (now - last) / 1000;
    last = now;
    state.sim.elapsed += raceMinutes;
    const predicted = state.sim.drift === 0 ? 0 : state.sim.elapsed / state.sim.drift;
    const km = kmAtMinute(state.packedPlan.segments, predicted);
    placeSim(km);
    if (state.sim.km >= state.packedPlan.distance_km - 0.01) {
      stopPlay(true);
      return;
    }
    state.sim.frame = requestAnimationFrame(tick);
  };
  state.sim.frame = requestAnimationFrame(tick);
}

function renderLocker() {
  const presets = document.querySelector("#locker-presets");
  const list = document.querySelector("#locker-list");
  presets.replaceChildren();
  for (const preset of PRESETS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "choice";
    const selected = state.locker.some((item) => item.name === preset.name && item.carbs_g === preset.carbs_g && item.caffeine_mg === preset.caffeine_mg);
    if (selected) {
      button.setAttribute("aria-checked", "true");
    }
    const dose = preset.kind === "liquid" ? `${preset.carbs_g} g/500 ml` : `${preset.carbs_g} g`;
    const caffeine = preset.caffeine_mg ? ` + ${preset.caffeine_mg} mg` : "";
    button.textContent = `${preset.name} ${dose}${caffeine}`;
    button.addEventListener("click", () => {
      if (selected) {
        state.locker = state.locker.filter((item) => item.name !== preset.name);
      } else if (state.locker.length < 12) {
        state.locker.push({ ...preset });
      }
      renderLocker();
    });
    presets.append(button);
  }
  list.replaceChildren();
  for (const item of state.locker) {
    const row = document.createElement("li");
    row.className = "locker-row";
    const label = document.createElement("span");
    label.textContent = `${item.name} · ${item.carbs_g} g · ${item.caffeine_mg} mg caffeine`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.locker = state.locker.filter((entry) => entry !== item);
      renderLocker();
    });
    row.append(label, remove);
    list.append(row);
  }
  const weight = Number(ui.weight.value);
  const cap = Number.isFinite(weight) ? Math.round(weight * 5) : 0;
  document.querySelector("#caffeine-cap").textContent = `Caffeine ceiling ${cap} mg (5 mg per kg).`;
}

function addCustomItem() {
  const name = document.querySelector("#custom-name").value.trim();
  const carbs = Number(document.querySelector("#custom-carbs").value);
  const caffeine = Number(document.querySelector("#custom-caffeine").value || 0);
  if (!name || !(carbs > 0 && carbs <= 120) || !(caffeine >= 0 && caffeine <= 400)) {
    setStatus("A custom item needs a name, 1–120 g of carbs, and 0–400 mg of caffeine.");
    return;
  }
  if (state.locker.length >= 12) {
    setStatus("The locker holds 12 products.");
    return;
  }
  state.locker.push({ name, carbs_g: carbs, caffeine_mg: caffeine, kind: "gel" });
  document.querySelector("#custom-name").value = "";
  document.querySelector("#custom-carbs").value = "";
  document.querySelector("#custom-caffeine").value = "";
  renderLocker();
}

function setFuelPreference(value) {
  state.fuelPreference = value || "gels_first";
  for (const button of document.querySelectorAll(".fuel-pref")) {
    if (button.dataset.preference === state.fuelPreference) {
      button.setAttribute("aria-checked", "true");
    } else {
      button.removeAttribute("aria-checked");
    }
  }
}

function exportRaceCard() {
  const plan = state.packedPlan;
  if (!plan) {
    return;
  }
  let cursor = plan.clock_start_min;
  const rows = plan.aid_legs.map((leg) => {
    cursor += leg.predicted_minutes;
    const station = state.aidStations.find((item) => item.name === leg.to_name);
    const cutoff = station?.cutoff_hours ? `${station.cutoff_hours} h` : "—";
    const vest = leg.pack.map((item) => `${item.quantity}× ${item.item}`).join(", ");
    const eat = leg.station_meal ? `${Math.round(leg.station_carbs_g)} g · ${leg.station_meal}` : "—";
    return `<tr>
      <td>${leg.to_name}<br>${leg.to_km.toFixed(1)} km</td>
      <td>${formatDuration(cursor)}</td>
      <td>${cutoff}</td>
      <td>${vest}</td>
      <td>${eat}</td>
    </tr>`;
  }).join("");
  const preference = state.fuelPreference.replaceAll("_", " ");
  ui.raceCard.classList.remove("hidden");
  ui.raceCard.innerHTML = `
    <h2>${plan.race_name}</h2>
    <p class="mt-1">Predicted ${formatDuration(plan.predicted_duration_min)} · ${preference} · caffeine ${Math.round(plan.totals?.caffeine_mg || 0)} / ${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg</p>
    <table class="mt-3">
      <thead><tr><th>Station</th><th>ETA</th><th>Cutoff</th><th>Vest carry</th><th>Eat at station</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <button id="print-card" class="thumb-btn mt-3" type="button">Print</button>`;
  ui.raceCard.querySelector("#print-card").addEventListener("click", () => window.print());
  ui.raceCard.scrollIntoView({ block: "start" });
}

function bind() {
  document.querySelector("#setup-form").addEventListener("submit", buildPlan);
  document.querySelector("#source-race").addEventListener("click", () => setSource("race"));
  document.querySelector("#source-gpx").addEventListener("click", () => setSource("gpx"));
  ui.raceSelect.addEventListener("change", () => {
    state.raceId = ui.raceSelect.value;
    applyTargetDefault();
  });
  ui.gpxFile.addEventListener("change", async () => {
    const file = ui.gpxFile.files?.[0];
    if (!file) {
      return;
    }
    state.gpxXml = await file.text();
    state.gpxName = file.name;
    ui.gpxName.textContent = `Loaded ${file.name}`;
    setSource("gpx");
  });
  for (const input of [ui.hours, ui.minutes]) {
    input.addEventListener("input", () => {
      state.targetEdited = true;
    });
  }
  for (const button of document.querySelectorAll(".gut")) {
    button.addEventListener("click", () => setCarbs(Number(button.dataset.carbs)));
  }
  document.querySelector("#nav-setup").addEventListener("click", () => showTab("setup"));
  document.querySelector("#nav-map").addEventListener("click", () => showTab("map"));
  document.querySelector("#nav-live").addEventListener("click", () => showTab("live"));
  ui.countdown.addEventListener("click", startClock);
  ui.log.addEventListener("click", () => logIntake().catch((error) => setStatus(error.message)));
  ui.gps.addEventListener("click", () => updateGps());
  ui.rescue.addEventListener("click", () => triggerRescue());
  document.querySelector("#add-locker").addEventListener("click", addCustomItem);
  ui.exportCard.addEventListener("click", exportRaceCard);
  for (const button of document.querySelectorAll(".fuel-pref")) {
    button.addEventListener("click", () => setFuelPreference(button.dataset.preference));
  }
  ui.weight.addEventListener("input", renderLocker);
  for (const range of document.querySelectorAll("[data-desk-range]")) {
    range.addEventListener("input", onDeskRange);
  }
  for (const button of document.querySelectorAll("[data-drift]")) {
    button.addEventListener("click", () => applyDrift(Number(button.dataset.drift)));
  }
  for (const button of document.querySelectorAll("[data-desk-play]")) {
    button.addEventListener("click", togglePlay);
  }
}

async function init() {
  bind();
  setCarbs(60);
  setSource("race");
  try {
    const session = await loadSession();
    restore(session);
  } catch (error) {
    setStatus("Saved race data on this phone could not be read.");
  }
  try {
    await loadRaces();
    if (state.raceId) {
      ui.raceSelect.value = state.raceId;
    }
    applyTargetDefault();
  } catch (error) {
    setStatus(error.message);
  }
  renderLocker();
  renderPacking();
  renderLive();
  ensureTicker();
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }
}

init();
