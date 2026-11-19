/** GPS watch, recalibration, GI rescue, and the desk simulator. */

import { announce, primeAudio, releaseWakeLock, requestWakeLock } from "./alerts.js";
import { recalibrate } from "./engine_client.js";
import { paintRaceControls, renderLive, startClock } from "./live.js";
import { positionOnCourse } from "./map_controller.js?v=20";
import { drawMap, moveRunner, showRealDot } from "./map_screen.js";
import { snapToSegments } from "./offline_engine.js";
import { currentSegment } from "./plan_cursor.js";
import { courseBody } from "./plan_request.js";
import { persist } from "./session.js";
import { state, ui } from "./state.js";
import { elapsedMin, formatDuration, setStatus, wallElapsedMin } from "./ui_helpers.js";

export const RECAL_EVERY_MS = 30000;

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
  state.autoGps = true;
  armGpsWatch();
  requestWakeLock();
  paintRaceControls();
  setStatus("Race Mode on. GPS is tracking.");
}

export function stopRaceMode() {
  state.raceMode = false;
  if (!state.autoGps && state.watchId != null && navigator.geolocation) {
    navigator.geolocation.clearWatch(state.watchId);
    state.watchId = null;
  }
  state.pendingAuto = null;
  state.autoLogAt = 0;
  releaseWakeLock();
  paintRaceControls();
  setStatus("Race Mode off.");
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
  if (state.watchId != null) {
    return;
  }
  state.autoGps = true;
  state.autoGpsError = "";
  state.watchId = navigator.geolocation.watchPosition(onRaceGps, onRaceGpsError, {
    enableHighAccuracy: true,
    timeout: 20000,
    maximumAge: 5000,
  });
}

export function onRaceGps(pos) {
  state.lastGpsAt = Date.now();
  state.autoGpsError = "";
  if (state.gpsDragging || !state.packedPlan) {
    paintAutoGps();
    return;
  }
  const lat = pos.coords.latitude;
  const lon = pos.coords.longitude;
  const accuracy = pos.coords.accuracy;
  state.realGps = { latitude: lat, longitude: lon, accuracy };
  state.gpsAccuracy = accuracy;
  showRealDot();
  let offCourseM = 0;
  try {
    offCourseM = snapToSegments(state.packedPlan.segments, lat, lon).offCourseM;
  } catch (error) {
    offCourseM = 0;
  }
  paintGps(`GPS Locked: ${lat.toFixed(5)}, ${lon.toFixed(5)} (±${Math.round(accuracy)}m)`, "ok");
  if (offCourseM > 250) {
    paintOffCourse(offCourseM);
    return;
  }
  paintOffCourse(0);
  if (state.sim.playing) {
    stopPlay(false);
  }
  state.sim.active = false;
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

export function onRaceGpsError(err) {
  const now = Date.now();
  const denied = err && (err.code === 1 || err.code === 2);
  state.autoGpsError = denied ? "GPS unavailable · permission denied" : "GPS unavailable · waiting for a fix";
  paintAutoGps();
  if (now - state.lastGpsErrorAt < 15000) {
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
  if (state.autoGpsError) {
    ui.autoGps.classList.add("warn");
    ui.autoGpsText.textContent = "GPS unavailable";
    return;
  }
  if (state.offTrailM > 250) {
    ui.autoGps.classList.add("warn");
    const km = state.offTrailM / 1000;
    const digits = km >= 10 ? 0 : 1;
    ui.autoGpsText.textContent = `${km.toFixed(digits)} km off trail${acc}`;
    return;
  }
  if (!state.lastGpsAt) {
    ui.autoGpsText.textContent = "Waiting for GPS";
    return;
  }
  ui.autoGps.classList.add("ok");
  ui.autoGpsText.textContent = `On trail${acc}`;
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
    body: `${Math.round(nearest.away)} m ahead`,
    speech: `${nearest.aid.name} in ${Math.round(nearest.away)} meters.`,
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
  ui.offCourse.textContent = `OFF-COURSE WARNING · ${km.toFixed(digits)} km from ${trail}`;
  paintAutoGps();
}

// iOS only shows the location prompt when getCurrentPosition runs in the tap itself.
export function onUpdateGpsClick() {
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
  state.realGps = { latitude: lat, longitude: lon, accuracy: pos.coords.accuracy };
  state.gpsAccuracy = pos.coords.accuracy;
  state.autoGpsError = "";
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

export function readGps() {
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

export function stopPlay(recalibrate) {
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
