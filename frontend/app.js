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
  realGps: null,
  sim: { active: false, km: 0, drift: 1, elapsed: 0, playing: false, frame: 0, busy: false },
  raceMode: false,
  alerts: false,
  watchId: null,
  wakeLock: null,
  wakeWarned: false,
  lastRecalAt: 0,
  recalBusy: false,
  lastGpsErrorAt: 0,
  headsUp: new Set(),
  dueFired: new Set(),
  aidPinged: new Set(),
  pendingAuto: null,
  autoLogAt: 0,
  lastAutoLog: null,
  audioCtx: null,
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
  form: document.querySelector("#setup-form"),
  setupSummary: document.querySelector("#setup-summary"),
  setupSummaryText: document.querySelector("#setup-summary-text"),
  gpsBanner: document.querySelector("#gps-banner"),
  offCourse: document.querySelector("#off-course"),
  toast: document.querySelector("#toast"),
  raceMode: document.querySelector("#race-mode"),
  undoAuto: document.querySelector("#undo-autolog"),
  alerts: document.querySelector("#enable-alerts"),
  testAlert: document.querySelector("#test-alert"),
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
  const text = message || "";
  ui.status.textContent = text;
  if (ui.toast) {
    ui.toast.textContent = text;
  }
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

function setupSummaryLine() {
  const plan = state.packedPlan;
  const km = Math.round(plan.distance_km);
  let label = `${km}km`;
  if (/salomon/i.test(plan.race_name)) label = `Salomon ${km}km`;
  else if (/vitosha/i.test(plan.race_name)) label = `Vitosha ${km}km`;
  const weight = Math.round(Number(plan.athlete.weight_kg));
  const carbs = Math.round(plan.athlete.carbs_g_per_hour);
  const temp = Math.round(Number(plan.athlete.temperature_c));
  const hours = Number(ui.hours.value) || 0;
  const minutes = Number(ui.minutes.value) || 0;
  const target = `${hours}h${String(minutes).padStart(2, "0")}m`;
  const name = document.createElement("b");
  name.textContent = label;
  const meta = document.createElement("span");
  meta.textContent = `${weight}kg · ${target} · ${carbs}g/h · ${temp}°C`;
  ui.setupSummaryText.replaceChildren(name, meta);
}

function collapseSetup() {
  if (!state.packedPlan) {
    return;
  }
  setupSummaryLine();
  ui.setupSummary.classList.remove("hidden");
  ui.setupSummary.classList.remove("is-open");
  ui.setupSummary.setAttribute("aria-expanded", "false");
  ui.setupSummary.querySelector(".edit-label").textContent = "Edit Setup ▾";
  ui.form.classList.add("hidden");
}

function showSetupForm() {
  ui.form.classList.remove("hidden");
  ui.setupSummary.classList.add("hidden");
  ui.setupSummary.classList.remove("is-open");
  ui.setupSummary.setAttribute("aria-expanded", "false");
}

function toggleSetup() {
  if (!state.packedPlan) {
    showSetupForm();
    return;
  }
  ui.setupSummary.classList.remove("hidden");
  const open = ui.form.classList.toggle("hidden") === false;
  ui.setupSummary.classList.toggle("is-open", open);
  ui.setupSummary.setAttribute("aria-expanded", open ? "true" : "false");
  ui.setupSummary.querySelector(".edit-label").textContent = open ? "Edit Setup ▴" : "Edit Setup ▾";
}

function renderPacking() {
  const plan = state.packedPlan;
  ui.packing.replaceChildren();
  ui.warnings.replaceChildren();
  if (!plan) {
    ui.summary.classList.add("hidden");
    ui.exportCard.disabled = true;
    ui.exportCard.classList.add("hidden");
    ui.raceCard.classList.add("hidden");
    showSetupForm();
    return;
  }
  const athlete = plan.athlete;
  ui.summary.classList.remove("hidden");
  ui.summary.textContent = `${plan.race_name} · ${formatDuration(plan.predicted_duration_min)} · ${athlete.flat_pace_min_per_km.toFixed(2)} min/km · caffeine ${Math.round(plan.totals?.caffeine_mg || 0)}/${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg`;
  for (const warning of plan.warnings) {
    const item = document.createElement("li");
    item.textContent = warning;
    ui.warnings.append(item);
  }
  for (const leg of plan.aid_legs) {
    const card = document.createElement("article");
    card.className = "pack-card";
    const chips = leg.pack.map((item) => `<li>${chipLabel(item)}</li>`).join("");
    const eat = eatLabel(leg);
    card.innerHTML = `
      <div class="pack-top">
        <h2>${leg.from_name} → ${leg.to_name} <span class="km">(${leg.distance_km.toFixed(1)} km)</span></h2>
        <span class="time-badge">${compactHours(leg.predicted_minutes)} · +${legGain(leg)}m</span>
      </div>
      <div class="macros">
        <span>${grouped(leg.carbs_g)}g Carbs</span>
        <span>${grouped(leg.fluid_ml)}ml Fluid</span>
        <span>${grouped(leg.sodium_mg)}mg Na</span>
      </div>
      <div class="pack-split">
        <div>
          <p class="kicker">Pack in vest</p>
          <ul class="chip-list">${chips || "<li>—</li>"}</ul>
        </div>
        <div>
          <p class="kicker">Eat at station</p>
          <p class="eat-note">${eat}</p>
        </div>
      </div>`;
    ui.packing.append(card);
  }
  ui.exportCard.disabled = false;
  ui.exportCard.classList.remove("hidden");
  collapseSetup();
}

function grouped(value) {
  return Math.round(value).toLocaleString("en-US");
}

function compactHours(minutes) {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours > 0 ? `${hours}h ${String(mins).padStart(2, "0")}m` : `${mins}m`;
}

function legGain(leg) {
  const segments = state.packedPlan?.segments || [];
  const from = leg.from_km * 1000;
  const to = leg.to_km * 1000;
  let gain = 0;
  for (const segment of segments) {
    if (segment.end_distance_m <= from || segment.start_distance_m >= to) continue;
    gain += segment.elevation_gain_m || 0;
  }
  return Math.round(gain);
}

function chipLabel(item) {
  const name = item.item
    .replace("Soft flask 500 ml", "500ml Flask")
    .replace("Salt capsule", "Salt");
  return `${item.quantity}× ${name}`;
}

function eatLabel(leg) {
  if (!leg.station_meal) return "—";
  if (leg.station_meal.startsWith("Aid Station Plate")) {
    return `Potatoes / banana + cola (${Math.round(leg.station_carbs_g)}g)`;
  }
  return `${leg.station_meal} (${Math.round(leg.station_carbs_g)}g)`;
}

function shortIntake(stop) {
  if (stop.at_aid_station) return `Aid plate (${Math.round(stop.carbs_g)}g)`;
  if (stop.form === "rescue_sip" || (stop.form === "sip" && stop.carbs_g === 0)) return "Water and salt sip";
  if (stop.form === "micro_dose" || stop.form === "liquid") return `Liquid ${Math.round(stop.carbs_g)}g · small sips`;
  const name = stop.product || "Gel";
  return `Take 1× ${name} (${Math.round(stop.carbs_g)}g)`;
}

function flavorTag(stop) {
  if (!stop) return "";
  if (stop.form === "micro_dose" || stop.form === "liquid") return "Liquid Micro-Dose";
  if (stop.form === "rescue_sip" || (stop.form === "sip" && stop.carbs_g === 0)) return "Sip";
  if (stop.flavor === "Savoury/Salty") return "Savoury";
  if (stop.flavor) return stop.flavor;
  if (stop.form === "rescue_sip" || stop.form === "sip") return "Sip";
  return "";
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
  paintRaceControls();
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
    ui.caption.textContent = "";
    ui.intake.textContent = "Build a plan";
    paintBadge(null);
    ui.nextAid.textContent = "—";
    return;
  }
  if (!clockOn) {
    ui.countdown.textContent = stop ? formatClock(stop.scheduled_minute) : "Done";
    ui.caption.textContent = stop ? (flavorTag(stop) || "Tap clock") : "";
  } else if (!stop) {
    ui.countdown.textContent = "Done";
    ui.caption.textContent = "";
  } else {
    const remaining = stop.scheduled_minute - elapsed;
    ui.countdown.textContent = remaining > 0 ? formatClock(remaining) : "Due";
    ui.caption.textContent = flavorTag(stop);
  }
  ui.intake.textContent = stop ? shortIntake(stop) : "";
  paintBadge(currentSegment());
  const aid = nextAid(clockOn ? elapsed : 0);
  ui.nextAid.textContent = aid ? compactHours(aid.etaMin) : "—";
  ui.nextAid.title = aid ? `${aid.name} · ${aid.km.toFixed(1)} km` : "";
}

function paintBadge(segment) {
  ui.badge.className = "zone-badge";
  if (!segment) {
    ui.badge.classList.add("idle");
    ui.badge.textContent = "—";
    ui.grade.textContent = "—";
    return;
  }
  ui.badge.classList.add(segment.zone);
  ui.badge.textContent = segment.zone.toUpperCase();
  const sign = segment.grade_percent >= 0 ? "+" : "";
  ui.grade.textContent = `${sign}${segment.grade_percent.toFixed(1)}%`;
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
  document.body.dataset.tab = name;
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
    const liveOpen = !document.querySelector("#tab-live").classList.contains("hidden");
    if (liveOpen) {
      renderLive();
    }
    if (state.raceMode || state.alerts) {
      raceCoachTick();
    }
  }, 1000);
}

async function logIntake(options = {}) {
  const stop = nextStop();
  if (!stop) {
    setStatus("No intake left to log.");
    return;
  }
  if (!state.startedAt) {
    startClock();
  }
  if (state.pendingAuto === stop.sequence) {
    state.pendingAuto = null;
    state.autoLogAt = 0;
  }
  state.logged.push(stop.sequence);
  state.dueFired.add(stop.sequence);
  if (options.auto) {
    state.lastAutoLog = stop.sequence;
    setStatus(`Auto-logged ${stop.carbs_g.toFixed(0)} g at ${stop.distance_km.toFixed(1)} km.`);
  } else {
    state.lastAutoLog = null;
    setStatus(`Logged ${stop.carbs_g.toFixed(0)} g at ${stop.distance_km.toFixed(1)} km.`);
  }
  paintRaceControls();
  renderLive();
  await persist();
}

function undoAutoLog() {
  if (state.lastAutoLog == null) {
    return;
  }
  const sequence = state.lastAutoLog;
  const stillThere = state.livePlan?.fuel_stops.some((stop) => stop.sequence === sequence);
  state.logged = state.logged.filter((item) => item !== sequence);
  state.dueFired.delete(sequence);
  state.headsUp.delete(sequence);
  state.lastAutoLog = null;
  state.pendingAuto = null;
  state.autoLogAt = 0;
  setStatus(stillThere ? "Last auto-log undone." : "That intake was already replaced by a GPS recalibration.");
  paintRaceControls();
  renderLive();
  persist();
}

const RECAL_EVERY_MS = 45000;

function paintRaceControls() {
  const racing = state.raceMode;
  ui.raceMode.setAttribute("aria-pressed", racing ? "true" : "false");
  ui.raceMode.textContent = racing ? "■ Stop Hands-Free Race Mode" : "▶ Start Hands-Free Race Mode";
  ui.alerts.setAttribute("aria-pressed", state.alerts ? "true" : "false");
  ui.undoAuto.classList.toggle("hidden", state.lastAutoLog == null);
}

function onRaceModeClick() {
  if (state.raceMode) {
    stopRaceMode();
    return;
  }
  if (!state.livePlan) {
    setStatus("Build a plan before Race Mode.");
    return;
  }
  if (!navigator.geolocation) {
    paintGps("GPS Error (0): This browser has no GPS. Check iPhone Settings > Privacy > Location Services > Safari Websites.", "error");
    return;
  }
  primeAudio();
  state.raceMode = true;
  state.lastRecalAt = 0;
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
  if (!state.startedAt) {
    startClock();
  }
  paintGps("Acquiring iPhone GPS...", "");
  state.watchId = navigator.geolocation.watchPosition(onRaceGps, onRaceGpsError, {
    enableHighAccuracy: true,
    timeout: 20000,
    maximumAge: 5000,
  });
  requestWakeLock();
  paintRaceControls();
  setStatus("Race Mode on. GPS is tracking.");
}

function stopRaceMode() {
  if (state.watchId != null && navigator.geolocation) {
    navigator.geolocation.clearWatch(state.watchId);
  }
  state.watchId = null;
  state.raceMode = false;
  state.pendingAuto = null;
  state.autoLogAt = 0;
  releaseWakeLock();
  paintRaceControls();
  setStatus("Race Mode off.");
}

function onRaceGps(pos) {
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  const accuracy = pos.coords.accuracy;
  state.realGps = { latitude: lat, longitude: lon, accuracy };
  paintGps(`GPS Locked: ${lat.toFixed(5)}, ${lon.toFixed(5)} (±${Math.round(accuracy)}m)`, "ok");
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
  showRealDot();
  noteAidDistance(lat, lon);
  const now = Date.now();
  if (state.recalBusy || (state.lastRecalAt && now - state.lastRecalAt < RECAL_EVERY_MS)) {
    return;
  }
  state.lastRecalAt = now;
  state.recalBusy = true;
  if (!state.startedAt) {
    startClock();
  }
  sendFix(lat, lon, false, "gps").catch((error) => {
    if (error.message !== "No connection to the fueling server.") {
      setStatus(error.message);
    }
  }).finally(() => {
    state.recalBusy = false;
  });
}

function onRaceGpsError(err) {
  const now = Date.now();
  if (now - state.lastGpsErrorAt < 15000) {
    return;
  }
  state.lastGpsErrorAt = now;
  onGpsError(err);
}

function noteAidDistance(latitude, longitude) {
  if (!state.alerts || !state.packedPlan || !state.aidStations.length) {
    return;
  }
  let distanceM = state.snap ? state.snap.distanceM : null;
  try {
    distanceM = snapToSegments(state.packedPlan.segments, latitude, longitude).distanceM;
  } catch (error) {
    if (distanceM == null) {
      return;
    }
  }
  let nearest = null;
  for (const aid of state.aidStations) {
    if (/finish/i.test(aid.name) || state.aidPinged.has(aid.name)) {
      continue;
    }
    const away = aid.distance_km * 1000 - distanceM;
    if (away > 0 && away < 500 && (!nearest || away < nearest.away)) {
      nearest = { aid, away };
    }
  }
  if (!nearest) {
    return;
  }
  state.aidPinged.add(nearest.aid.name);
  announce({
    title: `Aid · ${nearest.aid.name}`,
    body: `${Math.round(nearest.away)} m ahead`,
    speech: `${nearest.aid.name} in ${Math.round(nearest.away)} meters.`,
    tag: `aid-${nearest.aid.name}`,
  });
}

function raceCoachTick() {
  if (!state.startedAt || state.sim.active || !state.livePlan) {
    return;
  }
  const stop = nextStop();
  if (!stop) {
    return;
  }
  const remainingSec = (stop.scheduled_minute - elapsedMin()) * 60;
  if (state.alerts && remainingSec <= 120 && remainingSec >= 90 && !state.headsUp.has(stop.sequence)) {
    state.headsUp.add(stop.sequence);
    const name = itemTitle(stop);
    announce({
      title: `⏳ Heads up: ${name} in 2 min`,
      body: shortIntake(stop),
      speech: `Heads up. ${name} in 2 minutes.`,
      tag: `headsup-${stop.sequence}`,
    });
  }
  const due = remainingSec <= 0;
  if (due && (state.raceMode || state.alerts) && !state.dueFired.has(stop.sequence)) {
    state.dueFired.add(stop.sequence);
    announce({
      title: `⛽ FUEL NOW: ${itemTitle(stop)} (${String(stop.zone || "").toUpperCase()})`,
      body: shortIntake(stop),
      speech: coachSpeech(stop),
      tag: `now-${stop.sequence}`,
    });
  }
  if (!state.raceMode || !due) {
    return;
  }
  if (state.pendingAuto !== stop.sequence) {
    state.pendingAuto = stop.sequence;
    state.autoLogAt = Date.now() + (remainingSec < -30 ? 0 : 30000);
  }
  if (Date.now() >= state.autoLogAt && !state.logged.includes(stop.sequence)) {
    state.pendingAuto = null;
    logIntake({ auto: true }).catch((error) => setStatus(error.message));
  }
}

function itemTitle(stop) {
  if (stop.at_aid_station) return "Aid plate";
  if (stop.form === "micro_dose" || stop.form === "liquid") return "Liquid";
  if (stop.carbs_g === 0) return "Sip";
  return stop.product || "Gel";
}

function coachSpeech(stop) {
  const grade = Number(stop.grade_percent) || 0;
  const sign = grade >= 0 ? "plus" : "minus";
  const zone = String(stop.zone || "green").toLowerCase();
  const gradeBit = `Grade ${sign} ${Math.abs(grade).toFixed(0)} percent, ${zone} zone.`;
  if (stop.at_aid_station) {
    return `Fuel time. Aid station plate, ${Math.round(stop.carbs_g)} grams. ${gradeBit}`;
  }
  if (stop.carbs_g === 0) {
    return `Fuel time. Water and salt sip. ${gradeBit}`;
  }
  return `Fuel time. Take ${Math.round(stop.carbs_g)} grams ${stop.product || "gel"}. ${gradeBit}`;
}

function primeAudio() {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) {
    return null;
  }
  if (!state.audioCtx) {
    state.audioCtx = new Ctx();
  }
  if (state.audioCtx.state === "suspended") {
    state.audioCtx.resume();
  }
  if (window.speechSynthesis) {
    const unlock = new SpeechSynthesisUtterance(" ");
    unlock.volume = 0;
    window.speechSynthesis.speak(unlock);
  }
  return state.audioCtx;
}

function playChime() {
  const ctx = primeAudio();
  if (!ctx) {
    return;
  }
  const now = ctx.currentTime;
  [0, 0.18].forEach((offset, index) => {
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = index === 0 ? 880 : 1175;
    gain.gain.setValueAtTime(0.0001, now + offset);
    gain.gain.exponentialRampToValueAtTime(0.2, now + offset + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + offset + 0.16);
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start(now + offset);
    osc.stop(now + offset + 0.17);
  });
}

function speak(text) {
  if (!window.speechSynthesis || !text) {
    return;
  }
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(text);
  utter.lang = "en-US";
  utter.rate = 1;
  window.speechSynthesis.speak(utter);
}

function announce(note) {
  playChime();
  window.setTimeout(() => speak(note.speech), 400);
  pushNote(note.title, note.body, note.tag);
}

async function pushNote(title, body, tag) {
  if (!("Notification" in window) || Notification.permission !== "granted") {
    return;
  }
  const options = {
    body,
    icon: "/icons/icon-192.png",
    badge: "/icons/icon-192.png",
    tag: tag || "race-fuel",
    renotify: true,
  };
  try {
    const registration = await navigator.serviceWorker.ready;
    await registration.showNotification(title, options);
  } catch (error) {
    try {
      new Notification(title, options);
    } catch (fallbackError) {
      setStatus("The alert could not be posted to the lock screen.");
    }
  }
}

async function ensureNotifyPermission() {
  if (!("Notification" in window)) {
    return false;
  }
  if (Notification.permission === "granted") {
    return true;
  }
  if (Notification.permission === "denied") {
    return false;
  }
  try {
    return (await Notification.requestPermission()) === "granted";
  } catch (error) {
    return false;
  }
}

function alertsBlockedMessage() {
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (!standalone) {
    return "On iPhone, add this app to the Home Screen, then enable alerts so they reach the lock screen.";
  }
  return "Notifications are blocked. Allow them in iPhone Settings > Notifications.";
}

async function onAlertsClick() {
  primeAudio();
  if (state.alerts) {
    state.alerts = false;
    paintRaceControls();
    setStatus("Race alerts off.");
    return;
  }
  const granted = await ensureNotifyPermission();
  state.alerts = true;
  paintRaceControls();
  setStatus(granted ? "Race alerts on. Lock screen and voice." : alertsBlockedMessage());
}

async function onTestAlert() {
  primeAudio();
  const granted = await ensureNotifyPermission();
  playChime();
  window.setTimeout(() => {
    speak("Fuel time. Take 25 grams gel. Grade plus 4 percent, green zone.");
  }, 400);
  if (granted) {
    await pushNote("⛽ FUEL NOW: Gel (GREEN)", "Test alert. Take 25 grams gel.", "test-alert");
    setStatus("Test alert sent. Listen for the coach and check the lock screen.");
    return;
  }
  setStatus(`Chime and voice played. ${alertsBlockedMessage()}`);
}

async function requestWakeLock() {
  if (!navigator.wakeLock || !state.raceMode) {
    return;
  }
  try {
    state.wakeLock = await navigator.wakeLock.request("screen");
  } catch (error) {
    if (!state.wakeWarned) {
      state.wakeWarned = true;
      setStatus("The screen may still sleep. iOS did not grant a wake lock.");
    }
  }
}

function releaseWakeLock() {
  const lock = state.wakeLock;
  state.wakeLock = null;
  if (lock) {
    lock.release().catch(() => {});
  }
}

function paintGps(message, tone) {
  ui.gpsBanner.textContent = message;
  ui.gpsBanner.classList.remove("hidden", "error", "ok");
  if (tone) {
    ui.gpsBanner.classList.add(tone);
  }
}

function paintOffCourse(metres) {
  if (!(metres > 250)) {
    ui.offCourse.classList.add("hidden");
    ui.offCourse.textContent = "";
    return;
  }
  const km = metres / 1000;
  const digits = km >= 10 ? 0 : 1;
  const trail = /vitosha/i.test(state.packedPlan?.race_name || "") ? "the Vitosha trail" : "the race trail";
  ui.offCourse.classList.remove("hidden");
  ui.offCourse.textContent = `OFF-COURSE WARNING · ${km.toFixed(digits)} km from ${trail}`;
}

function showRealDot() {
  if (!state.realGps || !state.packedPlan) {
    return;
  }
  try {
    mapApi().setRealLocation(state.realGps.latitude, state.realGps.longitude);
  } catch (error) {
    setStatus(error.message);
  }
}

function wallElapsedMin() {
  if (!state.startedAt) {
    return 0;
  }
  return (Date.now() - state.startedAt) / 60000;
}

// iOS only shows the location prompt when getCurrentPosition runs in the tap itself.
function onUpdateGpsClick() {
  paintGps("Acquiring iPhone GPS...", "");
  if (!navigator.geolocation) {
    paintGps("GPS Error (0): This browser has no GPS. Check iPhone Settings > Privacy > Location Services > Safari Websites.", "error");
    return;
  }
  navigator.geolocation.getCurrentPosition(onGpsSuccess, onGpsError, {
    enableHighAccuracy: true,
    timeout: 10000,
    maximumAge: 0,
  });
}

function onGpsError(err) {
  const code = err && Number.isFinite(err.code) ? err.code : 0;
  const message = (err && err.message) || "Location request failed.";
  paintGps(`GPS Error (${code}): ${message}. Check iPhone Settings > Privacy > Location Services > Safari Websites.`, "error");
}

function onGpsSuccess(pos) {
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  state.realGps = { latitude: lat, longitude: lon, accuracy: pos.coords.accuracy };
  paintGps(`GPS Locked: ${lat.toFixed(5)}, ${lon.toFixed(5)} (±${Math.round(pos.coords.accuracy)}m)`, "ok");
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
  if (!state.startedAt) {
    startClock();
  }
  showRealDot();
  sendFix(lat, lon, false, "gps").catch((error) => {
    if (error.message === "No connection to the fueling server." && state.packedPlan) {
      paintGps(`GPS Locked: ${lat.toFixed(5)}, ${lon.toFixed(5)} (±${Math.round(pos.coords.accuracy)}m). No connection, so the plan was not rebuilt.`, "ok");
    } else {
      setStatus(error.message);
    }
  });
}

function readGps() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(Object.assign(new Error("This browser has no GPS."), { code: 0 }));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 10000,
      maximumAge: 0,
    });
  });
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
      const position = await readGps();
      latitude = position.coords.latitude;
      longitude = position.coords.longitude;
      state.realGps = {
        latitude,
        longitude,
        accuracy: position.coords.accuracy,
      };
      paintGps(`GPS Locked: ${latitude.toFixed(5)}, ${longitude.toFixed(5)} (±${Math.round(position.coords.accuracy)}m)`, "ok");
      showRealDot();
    } catch (error) {
      onGpsError(error);
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
    state.sim.active = false;
    await sendFix(latitude, longitude, true, "gps");
  } catch (error) {
    setStatus(error.message);
  } finally {
    ui.rescue.disabled = !state.livePlan;
  }
}

async function sendFix(latitude, longitude, giRescue, source = "sim") {
  const athlete = state.livePlan.athlete;
  const elapsed = source === "gps" ? wallElapsedMin() : elapsedMin();
  const body = {
    ...courseBody(athlete),
    latitude,
    longitude,
    elapsed_min: Number(elapsed.toFixed(3)),
    gi_rescue: giRescue,
  };
  try {
    const result = await postJson("/api/recalibrate", body);
    state.livePlan = result.remaining;
    state.logged = [];
    if (source === "gps") {
      state.headsUp.clear();
      state.dueFired.clear();
      state.pendingAuto = null;
      state.autoLogAt = 0;
      state.lastAutoLog = null;
    }
    state.giRescue = result.gi_rescue_applied;
    state.snap = {
      latitude: result.snap.latitude,
      longitude: result.snap.longitude,
      distanceM: result.snap.distance_along_m,
      offCourseM: result.snap.off_course_m,
      onCourse: result.snap.on_course,
    };
    if (source !== "gps" && state.sim.active) {
      state.sim.km = result.snap.distance_along_m / 1000;
    }
    if (source === "gps") {
      paintOffCourse(result.snap.off_course_m);
    }
    ui.liveMessage.textContent = result.message;
    setStatus(result.message);
  } catch (error) {
    if (error.message !== "No connection to the fueling server.") {
      throw error;
    }
    const snapped = snapToSegments(state.packedPlan.segments, latitude, longitude);
    state.snap = snapped;
    if (source === "gps") {
      paintOffCourse(snapped.offCourseM);
    }
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
      alerts: state.alerts,
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
  state.alerts = Boolean(session.alerts);
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
  if (state.raceMode) {
    setStatus("Stop Race Mode to use the desk simulator.");
    paintDesk();
    return;
  }
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
  if (state.raceMode) {
    setStatus("Stop Race Mode to use the desk simulator.");
    return;
  }
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
  if (state.raceMode) {
    setStatus("Stop Race Mode to use the desk simulator.");
    return;
  }
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
    button.className = "pill";
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
  document.querySelector("#caffeine-cap").textContent = `Cap ${cap} mg`;
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
  document.querySelector("#source-gpx").addEventListener("click", () => {
    setSource("gpx");
    ui.gpxFile.click();
  });
  ui.raceSelect.addEventListener("change", () => {
    setSource("race");
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
  ui.gps.addEventListener("click", onUpdateGpsClick);
  ui.setupSummary.addEventListener("click", toggleSetup);
  document.querySelector("#offline-tiles").addEventListener("click", () => {
    setStatus("Tiles stay on this phone after you view this course.");
  });
  document.querySelector("#center-me").addEventListener("click", () => {
    showTab("map");
    requestAnimationFrame(() => {
      if (!state.realGps) {
        setStatus("Tap Update GPS on the Live tab to drop your real location on the map.");
        return;
      }
      try {
        const view = mapApi();
        view.setRealLocation(state.realGps.latitude, state.realGps.longitude);
        view.centerOnMe();
      } catch (error) {
        setStatus(error.message);
      }
    });
  });
  ui.rescue.addEventListener("click", () => triggerRescue());
  ui.raceMode.addEventListener("click", onRaceModeClick);
  ui.undoAuto.addEventListener("click", () => undoAutoLog());
  ui.alerts.addEventListener("click", () => onAlertsClick());
  ui.testAlert.addEventListener("click", () => onTestAlert());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.raceMode) {
      requestWakeLock();
    }
  });
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
