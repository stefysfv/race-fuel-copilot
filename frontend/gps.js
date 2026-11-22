/** GPS watch, quality guard, adaptive polling, recalibration, GI rescue, and desk simulator. */

import { announce, playCountdownBeep, primeAudio, releaseWakeLock, requestWakeLock } from "./alerts.js";
import {
  recordBackgroundResume,
  recordGpsAccepted,
  recordGpsRejected,
  recordRecalibration,
  setPollingDiag,
} from "./battery_diag.js";
import { recalibrate } from "./engine_client.js";
import { createGpsGuard, haversineMeters, pollingPolicy } from "./gps_guard.js";
import { ensureTicker, paintRaceControls, renderLive, sectionForDistanceKm, startClock } from "./live.js";
import { legKey, renderCrewSheet } from "./crew_sheet.js";
import { renderPacking } from "./packing.js";
import { BATTERY_SAVER_KEY, readText, writeText } from "./local_store.js";
import { positionOnCourse } from "./map_controller.js?v=23";
import { drawMap, moveRunner, showRealDot } from "./map_screen.js";
import { snapToSegments } from "./offline_engine.js";
import { currentSegment, nextAid, nextStop } from "./plan_cursor.js";
import { courseBody } from "./plan_request.js";
import { persist } from "./session.js";
import { state, ui } from "./state.js";
import { elapsedMin, formatDuration, formatElapsedClock, setStatus, wallElapsedMin } from "./ui_helpers.js";
import { formatDistance, formatShortLength } from "./units.js";

export const RECAL_EVERY_MS = 30_000;
const CATCHUP_MIN_HIDDEN_MS = 10_000;
const CATCHUP_TIMEOUT_MS = 5_000;

const gpsGuard = createGpsGuard();
let catchUpTimer = 0;
let countdownTimer = 0;

export function getGpsGuard() {
  return gpsGuard;
}

export function currentPollingPolicy() {
  const elapsed = state.startedAt ? wallElapsedMin() : elapsedMin();
  const stop = nextStop();
  const stopEtaMin = stop ? Math.max(0, stop.scheduled_minute - elapsed) : Infinity;
  let aidDistanceM = Infinity;
  if (state.snap && state.aidStations?.length) {
    for (const aid of state.aidStations) {
      const away = aid.distance_km * 1000 - state.snap.distanceM;
      if (away >= 0 && away < aidDistanceM) aidDistanceM = away;
    }
  } else {
    const aid = nextAid(elapsed);
    if (aid && aid.etaMin <= 12) aidDistanceM = 900;
  }
  const policy = pollingPolicy({
    batterySaver: state.batterySaver,
    stopEtaMin,
    aidDistanceM,
    offCourseM: state.offTrailM,
  });
  state.pollingState = policy.state;
  setPollingDiag({ state: policy.state, mode: policy.mode, batterySaver: state.batterySaver });
  return policy;
}

export function loadBatterySaver() {
  state.batterySaver = readText(BATTERY_SAVER_KEY) === "1";
  applyBatterySaverUi();
}

export function applyBatterySaverUi() {
  document.body.classList.toggle("battery-saver", Boolean(state.batterySaver));
  document.body.classList.toggle("race-active", Boolean(state.raceMode));
  ui.batterySaver?.setAttribute("aria-pressed", state.batterySaver ? "true" : "false");
  ui.launchBatterySaver?.setAttribute("aria-pressed", state.batterySaver ? "true" : "false");
  setPollingDiag({ state: state.pollingState, mode: state.batterySaver ? "poll" : "watch", batterySaver: state.batterySaver });
}

export function setBatterySaver(enabled) {
  state.batterySaver = Boolean(enabled);
  writeText(BATTERY_SAVER_KEY, state.batterySaver ? "1" : "0");
  applyBatterySaverUi();
  refreshGpsPolling();
  drawMap();
  setStatus(state.batterySaver
    ? "Battery Saver on: OLED dark mode, slower GPS polling, offline tiles paused."
    : "Battery Saver off.");
}

function clearGpsWatchAndPoll() {
  if (state.watchId != null && navigator.geolocation) {
    navigator.geolocation.clearWatch(state.watchId);
    state.watchId = null;
  }
  if (state.gpsPollTimer != null) {
    window.clearInterval(state.gpsPollTimer);
    state.gpsPollTimer = null;
    state.gpsPollIntervalMs = 0;
  }
}

function pollOnce() {
  if (!navigator.geolocation || document.visibilityState === "hidden") return;
  navigator.geolocation.getCurrentPosition(onRaceGps, onRaceGpsError, {
    enableHighAccuracy: true,
    timeout: 15_000,
    maximumAge: 10_000,
  });
}

export function refreshGpsPolling() {
  if (!state.autoGps || state.gpsDragging || !state.packedPlan) {
    return;
  }
  if (!navigator.geolocation) {
    state.autoGpsError = "GPS unavailable · permission denied";
    paintAutoGps();
    return;
  }
  const policy = currentPollingPolicy();
  if (policy.mode === "poll") {
    if (state.watchId != null) {
      navigator.geolocation.clearWatch(state.watchId);
      state.watchId = null;
    }
    if (state.gpsPollTimer != null && state.gpsPollIntervalMs === policy.pollIntervalMs) {
      return;
    }
    if (state.gpsPollTimer != null) {
      window.clearInterval(state.gpsPollTimer);
    }
    state.gpsPollIntervalMs = policy.pollIntervalMs;
    state.gpsPollTimer = window.setInterval(pollOnce, policy.pollIntervalMs);
    return;
  }
  if (state.gpsPollTimer != null) {
    window.clearInterval(state.gpsPollTimer);
    state.gpsPollTimer = null;
    state.gpsPollIntervalMs = 0;
  }
  if (state.watchId != null) {
    return;
  }
  state.autoGpsError = "";
  state.watchId = navigator.geolocation.watchPosition(onRaceGps, onRaceGpsError, {
    enableHighAccuracy: true,
    timeout: 20_000,
    maximumAge: 10_000,
  });
}

function finishCountdown(resetStart) {
  window.clearTimeout(countdownTimer);
  countdownTimer = 0;
  state.raceCountdown = false;
  ui.raceCountdownOverlay?.classList.add("hidden");
  if (resetStart) {
    state.startedAt = Date.now();
    state.pausedAt = null;
    state.pausedTotalMs = 0;
  }
  ensureTicker();
  paintRaceControls();
  renderLive();
  persist();
}

export function skipStartCountdown() {
  if (!state.raceCountdown) return;
  playCountdownBeep(true);
  finishCountdown(true);
  setStatus("Race Mode live. GPS is tracking.");
}

function runStartCountdown() {
  window.clearTimeout(countdownTimer);
  const overlay = ui.raceCountdownOverlay;
  const num = ui.raceCountdownNum;
  if (!overlay || !num) {
    finishCountdown(true);
    return;
  }
  state.raceCountdown = true;
  const steps = ["3", "2", "1", "GO!"];
  let idx = 0;
  overlay.classList.remove("hidden");

  const tick = () => {
    if (!state.raceMode || !state.raceCountdown) {
      overlay.classList.add("hidden");
      return;
    }
    const label = steps[idx];
    const isGo = label === "GO!";
    num.textContent = label;
    num.classList.toggle("is-go", isGo);
    playCountdownBeep(isGo);
    idx += 1;
    if (isGo) {
      countdownTimer = window.setTimeout(() => {
        finishCountdown(true);
        setStatus("Race Mode live. GPS is tracking.");
      }, 420);
      return;
    }
    countdownTimer = window.setTimeout(tick, 680);
  };
  tick();
}

export function onRaceModeClick() {
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
  if (state.pausedAt) {
    resumeRaceMode();
    return;
  }
  primeAudio();
  const freshStart = !state.startedAt;
  state.raceMode = true;
  state.lastRecalAt = 0;
  state.lastRecalPos = null;
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
  if (freshStart) {
    state.startedAt = Date.now();
    state.pausedAt = null;
    state.pausedTotalMs = 0;
    state.intakeHistory = [];
  }
  paintGps("Acquiring GPS...", "");
  state.autoGps = true;
  applyBatterySaverUi();
  armGpsWatch();
  if (state.batterySaver) pollOnce();
  requestWakeLock();
  paintRaceControls();
  renderLive();
  if (freshStart) {
    runStartCountdown();
  } else {
    ensureTicker();
    setStatus("Race Mode on. GPS is tracking.");
  }
}

export function pauseRaceMode() {
  if (!state.startedAt || state.pausedAt) return;
  window.clearTimeout(countdownTimer);
  countdownTimer = 0;
  state.raceCountdown = false;
  ui.raceCountdownOverlay?.classList.add("hidden");
  state.pausedAt = Date.now();
  state.pendingAuto = null;
  state.autoLogAt = 0;
  clearGpsWatchAndPoll();
  releaseWakeLock();
  paintRaceControls();
  renderLive();
  persist();
  setStatus("Race paused. Tap Resume to continue or Restart to reset.");
}

export function resumeRaceMode() {
  if (!state.startedAt || !state.pausedAt) return;
  primeAudio();
  state.pausedTotalMs = (state.pausedTotalMs || 0) + Math.max(0, Date.now() - state.pausedAt);
  state.pausedAt = null;
  state.raceMode = true;
  state.autoGps = true;
  applyBatterySaverUi();
  armGpsWatch();
  if (state.batterySaver) pollOnce();
  requestWakeLock();
  ensureTicker();
  paintRaceControls();
  renderLive();
  persist();
  setStatus("Race resumed.");
}

export function togglePauseRace() {
  if (state.pausedAt) {
    resumeRaceMode();
  } else {
    pauseRaceMode();
  }
}

export function stopRaceMode() {
  window.clearTimeout(countdownTimer);
  countdownTimer = 0;
  state.raceCountdown = false;
  ui.raceCountdownOverlay?.classList.add("hidden");
  if (state.startedAt && !state.pausedAt) {
    state.pausedAt = Date.now();
  }
  state.raceMode = false;
  applyBatterySaverUi();
  clearGpsWatchAndPoll();
  state.pendingAuto = null;
  state.autoLogAt = 0;
  releaseWakeLock();
  paintRaceControls();
  renderLive();
  persist();
  setStatus("Race Mode paused. Tap Resume or Restart anytime.");
}

export function openRestartModal() {
  ui.restartModal?.classList.remove("hidden");
}

export function closeRestartModal() {
  ui.restartModal?.classList.add("hidden");
}

export async function confirmRestartRace() {
  closeRestartModal();
  window.clearTimeout(countdownTimer);
  countdownTimer = 0;
  state.raceCountdown = false;
  ui.raceCountdownOverlay?.classList.add("hidden");
  state.raceMode = false;
  state.startedAt = null;
  state.pausedAt = null;
  state.pausedTotalMs = 0;
  state.logged = [];
  state.intakeHistory = [];
  state.completedLegs = [];
  state.giRescueEvents = [];
  state.passedDistanceM = 0;
  state.sectionLogs = [];
  state.lastGpsRecalAt = 0;
  state.headsUp.clear();
  state.dueFired.clear();
  state.aidPinged.clear();
  state.pendingAuto = null;
  state.autoLogAt = 0;
  state.lastAutoLog = null;
  state.giRescue = false;
  state.lastRecalAt = 0;
  state.lastRecalPos = null;
  state.snap = null;
  state.weakGpsAccuracy = null;
  gpsGuard.reset();
  if (state.packedPlan) {
    state.livePlan = structuredClone(state.packedPlan);
    state.aidStations = Array.isArray(state.packedPlan.aid_stations) ? state.packedPlan.aid_stations : [];
  }
  clearGpsWatchAndPoll();
  releaseWakeLock();
  if (ui.liveMessage) ui.liveMessage.textContent = "";
  ui.gpsBanner?.classList.add("hidden");
  paintOffCourse(0);
  applyBatterySaverUi();
  paintRaceControls();
  renderPacking();
  drawMap();
  renderLive();
  await persist();
  setStatus("Race restarted to 0:00. Your plan is ready.");
}

export function armGpsWatch() {
  if (state.gpsDragging) {
    return;
  }
  if (!navigator.geolocation) {
    state.autoGpsError = "GPS unavailable · permission denied";
    paintAutoGps();
    return;
  }
  state.autoGps = true;
  state.autoGpsError = "";
  refreshGpsPolling();
}

function setCatchingUp(active, message = "") {
  state.catchingUp = Boolean(active);
  if (ui.catchingUp) {
    ui.catchingUp.textContent = active ? (message || "Catching up with clock and GPS...") : "";
    ui.catchingUp.classList.toggle("hidden", !active);
  }
}

function quietVeryStaleStops(elapsed) {
  const stops = state.livePlan?.fuel_stops || [];
  for (const stop of stops) {
    if (elapsed - stop.scheduled_minute > 10 && !state.logged.includes(stop.sequence)) {
      state.dueFired.add(stop.sequence);
      state.headsUp.add(stop.sequence);
    }
  }
}

export function handleVisibilityChange() {
  if (document.visibilityState === "hidden") {
    state.hiddenAt = Date.now();
    return;
  }
  const hiddenMs = state.hiddenAt ? Date.now() - state.hiddenAt : 0;
  state.hiddenAt = null;
  if (state.raceMode) {
    requestWakeLock();
  }
  if (!state.packedPlan || hiddenMs < CATCHUP_MIN_HIDDEN_MS) {
    return;
  }
  recordBackgroundResume();
  quietVeryStaleStops(wallElapsedMin());
  setCatchingUp(true, "Catching up with clock and GPS...");
  renderLive();
  window.clearTimeout(catchUpTimer);
  catchUpTimer = window.setTimeout(() => {
    setCatchingUp(false);
    renderLive();
  }, CATCHUP_TIMEOUT_MS);

  if (navigator.geolocation && (state.raceMode || state.autoGps)) {
    state.lastRecalAt = 0;
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        onRaceGps(pos);
        window.clearTimeout(catchUpTimer);
        setCatchingUp(false);
        renderLive();
      },
      () => {
        window.clearTimeout(catchUpTimer);
        setCatchingUp(false);
        renderLive();
      },
      { enableHighAccuracy: true, timeout: 8_000, maximumAge: 5_000 },
    );
  }
}

function shouldRecalibrate(lat, lon, now, policy) {
  if (state.recalBusy) return false;
  if (!state.lastRecalAt) return true;
  const elapsedMs = now - state.lastRecalAt;
  if (elapsedMs >= policy.forceRecalMs) return true;
  if (elapsedMs < policy.recalIntervalMs) return false;
  if (!state.lastRecalPos) return true;
  const movedM = haversineMeters(state.lastRecalPos.latitude, state.lastRecalPos.longitude, lat, lon);
  return movedM >= policy.minMoveM;
}

export function onRaceGps(pos) {
  const now = Number.isFinite(pos?.timestamp) ? pos.timestamp : Date.now();
  const policy = currentPollingPolicy();
  if (!state.catchingUp && state.lastFixProcessedAt && now - state.lastFixProcessedAt < policy.processThrottleMs) {
    return;
  }
  state.lastFixProcessedAt = now;

  if (state.gpsDragging || !state.packedPlan) {
    paintAutoGps();
    return;
  }
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  const accuracy = pos.coords.accuracy;

  const verdict = gpsGuard.evaluate({ latitude: lat, longitude: lon, accuracy, timestamp: now });
  if (!verdict.accepted) {
    recordGpsRejected(verdict.reason);
    if (verdict.reason === "accuracy") {
      state.weakGpsAccuracy = Number.isFinite(accuracy) ? accuracy : null;
      paintGps(`Weak GPS (±${Math.round(accuracy)}m) — holding plan`, "");
    } else if (verdict.reason === "jump") {
      paintGps("GPS jump ignored — holding plan", "");
    }
    paintAutoGps();
    return;
  }

  state.weakGpsAccuracy = null;
  state.lastGpsAt = now;
  state.autoGpsError = "";
  recordGpsAccepted(now);

  state.realGps = { latitude: lat, longitude: lon, accuracy };
  state.gpsAccuracy = accuracy;
  showRealDot();

  let offCourseM = 0;
  try {
    offCourseM = snapToSegments(state.packedPlan.segments, lat, lon).offCourseM;
  } catch (error) {
    offCourseM = 0;
  }
  paintGps(`GPS Locked (±${Math.round(accuracy)}m)`, "ok");
  if (offCourseM > 250) {
    paintOffCourse(offCourseM);
    refreshGpsPolling();
    return;
  }
  paintOffCourse(0);
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
  noteAidDistance(lat, lon);

  const updatedPolicy = currentPollingPolicy();
  refreshGpsPolling();

  if (!shouldRecalibrate(lat, lon, now, updatedPolicy)) {
    return;
  }
  state.lastRecalAt = now;
  state.lastRecalPos = { latitude: lat, longitude: lon };
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

export function onRaceGpsError(err) {
  const now = Date.now();
  const denied = err && (err.code === 1 || err.code === 2);
  state.autoGpsError = denied ? "GPS unavailable · permission denied" : "GPS unavailable · waiting for a fix";
  paintAutoGps();
  if (now - state.lastGpsErrorAt < 15_000) {
    return;
  }
  state.lastGpsErrorAt = now;
  onGpsError(err);
}

export function paintAutoGps() {
  if (!ui.autoGps || !ui.autoGpsText) {
    return;
  }
  ui.autoGps.classList.remove("ok", "warn");
  const acc = Number.isFinite(state.gpsAccuracy) ? ` · ±${Math.round(state.gpsAccuracy)}m` : "";
  if (!state.packedPlan) {
    ui.autoGpsText.textContent = "GPS starts with a plan";
    return;
  }
  if (state.catchingUp) {
    ui.autoGps.classList.add("warn");
    ui.autoGpsText.textContent = "Catching up...";
    return;
  }
  if (state.autoGpsError) {
    ui.autoGps.classList.add("warn");
    ui.autoGpsText.textContent = "GPS unavailable";
    return;
  }
  if (Number.isFinite(state.weakGpsAccuracy)) {
    ui.autoGps.classList.add("warn");
    ui.autoGpsText.textContent = `Weak GPS (±${Math.round(state.weakGpsAccuracy)}m)`;
    return;
  }
  if (state.offTrailM > 250) {
    ui.autoGps.classList.add("warn");
    const km = state.offTrailM / 1000;
    const digits = km >= 10 ? 0 : 1;
    ui.autoGpsText.textContent = `${formatDistance(km, { digits })} off trail${acc}`;
    return;
  }
  if (!state.lastGpsAt) {
    ui.autoGpsText.textContent = "Waiting for GPS";
    return;
  }
  ui.autoGps.classList.add("ok");
  const saver = state.batterySaver ? " · Saver" : "";
  ui.autoGpsText.textContent = `On trail${acc}${saver}`;
}

export function noteAidDistance(latitude, longitude) {
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
    body: `${formatShortLength(nearest.away)} ahead`,
    speech: `${nearest.aid.name} in ${formatShortLength(nearest.away)}.`,
    tag: `aid-${nearest.aid.name}`,
  });
}

export function paintGps(message, tone) {
  ui.gpsBanner.textContent = message;
  ui.gpsBanner.classList.remove("hidden", "error", "ok");
  if (tone) {
    ui.gpsBanner.classList.add(tone);
  }
}

export function paintOffCourse(metres) {
  state.offTrailM = metres > 250 ? metres : 0;
  if (!(metres > 250)) {
    ui.offCourse.classList.add("hidden");
    ui.offCourse.textContent = "";
    paintAutoGps();
    return;
  }
  const km = metres / 1000;
  const digits = km >= 10 ? 0 : 1;
  const trail = /vitosha/i.test(state.packedPlan?.race_name || "") ? "the Vitosha trail" : "the race trail";
  ui.offCourse.classList.remove("hidden");
  ui.offCourse.textContent = `OFF-COURSE WARNING · ${formatDistance(km, { digits })} from ${trail}`;
  paintAutoGps();
}

// iOS only shows the location prompt when getCurrentPosition runs in the tap itself.
export function onUpdateGpsClick() {
  paintGps("Acquiring GPS...", "");
  if (!navigator.geolocation) {
    paintGps("GPS Error (0): This browser has no GPS. Check iPhone Settings > Privacy > Location Services > Safari Websites.", "error");
    return;
  }
  navigator.geolocation.getCurrentPosition(onGpsSuccess, onGpsError, {
    enableHighAccuracy: true,
    timeout: 10_000,
    maximumAge: 0,
  });
}

export function onGpsError(err) {
  const code = err && Number.isFinite(err.code) ? err.code : 0;
  const message = (err && err.message) || "Location request failed.";
  state.autoGpsError = "GPS unavailable";
  paintAutoGps();
  paintGps(`GPS Error (${code}): ${message}. Check iPhone Settings > Privacy > Location Services > Safari Websites.`, "error");
}

export function onGpsSuccess(pos) {
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  const accuracy = pos.coords.accuracy;
  const now = Number.isFinite(pos?.timestamp) ? pos.timestamp : Date.now();

  const verdict = gpsGuard.evaluate({ latitude: lat, longitude: lon, accuracy, timestamp: now });
  if (!verdict.accepted) {
    recordGpsRejected(verdict.reason);
    if (verdict.reason === "accuracy") {
      state.weakGpsAccuracy = Number.isFinite(accuracy) ? accuracy : null;
      paintGps(`Weak GPS (±${Math.round(accuracy)}m) — holding plan`, "");
    } else {
      paintGps("GPS jump ignored — holding plan", "");
    }
    paintAutoGps();
    return;
  }

  state.weakGpsAccuracy = null;
  state.lastGpsAt = now;
  recordGpsAccepted(now);
  state.realGps = { latitude: lat, longitude: lon, accuracy };
  state.gpsAccuracy = accuracy;
  state.autoGpsError = "";
  paintGps(`GPS Locked (±${Math.round(accuracy)}m)`, "ok");
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
  if (!state.startedAt) {
    startClock();
  }
  showRealDot();
  state.lastRecalAt = now;
  state.lastRecalPos = { latitude: lat, longitude: lon };
  sendFix(lat, lon, false, "gps").catch((error) => {
    if (error.message === "No connection to the fueling server." && state.packedPlan) {
      paintGps(`GPS Locked: ${lat.toFixed(5)}, ${lon.toFixed(5)} (±${Math.round(accuracy)}m). No connection, so the plan was not rebuilt.`, "ok");
    } else {
      setStatus(error.message);
    }
  });
}

export function readGps() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(Object.assign(new Error("This browser has no GPS."), { code: 0 }));
      return;
    }
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: true,
      timeout: 10_000,
      maximumAge: 0,
    });
  });
}

export async function triggerRescue() {
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
      paintGps(`GPS Locked (±${Math.round(accuracy)}m)`, "ok");
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

export async function sendFix(latitude, longitude, giRescue, source = "sim") {
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
    const result = await recalibrate(body);
    recordRecalibration();
    state.livePlan = result.remaining;
    if (result.remaining.aid_stations?.length) {
      state.aidStations = result.remaining.aid_stations;
    }
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
      if (result.snap.off_course_m <= 250 && Number.isFinite(result.snap.distance_along_m)) {
        state.passedDistanceM = Math.max(state.passedDistanceM || 0, result.snap.distance_along_m);
        const passedKm = state.passedDistanceM / 1000;
        for (const leg of state.packedPlan?.aid_legs || []) {
          if (Number(leg.to_km) <= passedKm - 0.05) {
            const key = legKey(leg);
            if (!state.completedLegs.includes(key)) state.completedLegs.push(key);
          }
        }
      }
    }
    if (giRescue) {
      const distKm = (result.snap.distance_along_m || 0) / 1000;
      const sec = sectionForDistanceKm(distKm);
      state.giRescueEvents.push({
        clockText: formatElapsedClock(elapsed),
        distanceKm: distKm,
        sectionIndex: sec.index,
        sectionName: sec.name,
      });
    }
    state.lastGpsRecalAt = Date.now();
    const cleanMsg = (result.snap?.off_course_m > 500 && !giRescue)
      ? "Off course — sync GPS again when back on trail."
      : result.message;
    ui.liveMessage.textContent = cleanMsg;
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
  renderPacking();
  if (!ui.crewModal?.classList.contains("hidden")) {
    renderCrewSheet();
  }
  drawMap();
  renderLive();
  await persist();
}

export function driftLabel(drift) {
  if (drift === 0.9) return "Fast -10%";
  if (drift === 1.15) return "Slow +15%";
  if (drift === 1.3) return "Bonking +30%";
  return "On Target";
}

export function resetSim() {
  if (state.sim.frame) {
    cancelAnimationFrame(state.sim.frame);
  }
  gpsGuard.reset();
  state.lastRecalPos = null;
  state.weakGpsAccuracy = null;
  state.sim.active = false;
  state.sim.km = 0;
  state.sim.drift = 1;
  state.sim.elapsed = 0;
  state.sim.playing = false;
  state.sim.frame = 0;
  state.sim.busy = false;
}

export function minuteAtKm(segments, km) {
  const point = positionOnCourse(segments, km * 1000);
  if (!point) {
    return 0;
  }
  const segment = point.segment;
  const span = segment.end_distance_m - segment.start_distance_m;
  const fraction = span <= 0 ? 0 : (point.distanceM - segment.start_distance_m) / span;
  return segment.start_minute + fraction * segment.predicted_minutes;
}

export function kmAtMinute(segments, minute) {
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

export function paintDesk() {
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
      km.textContent = formatDistance(state.sim.km, { digits: 1 });
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

export function placeSim(km) {
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

export let deskScrub = 0;

export function onDeskRange(event) {
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

export async function applyDrift(factor) {
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

export function stopPlay(recalibrateAfter) {
  state.sim.playing = false;
  if (state.sim.frame) {
    cancelAnimationFrame(state.sim.frame);
    state.sim.frame = 0;
  }
  paintDesk();
  if (recalibrateAfter && state.sim.drift !== 1 && state.snap) {
    sendFix(state.snap.latitude, state.snap.longitude, state.giRescue).catch((error) => setStatus(error.message));
  }
}

export function togglePlay() {
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
