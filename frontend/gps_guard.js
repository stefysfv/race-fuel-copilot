/** GPS quality guard and adaptive polling policy for 12+ hour races. */

export const MAX_ACCURACY_M = 50;
export const MAX_SPEED_MPS = 8.5;
export const SHORT_JUMP_M = 300;
export const SHORT_JUMP_WINDOW_S = 30;
export const APPROACH_STOP_MIN = 10;
export const APPROACH_AID_M = 1000;
export const OFF_COURSE_M = 250;

const EARTH_RADIUS_M = 6371000;

export function haversineMeters(lat1, lon1, lat2, lon2) {
  const toRad = (deg) => (deg * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function isPlausibleStep(prev, next, maxSpeedMps, shortJumpM, shortJumpWindowS) {
  const dtSec = Math.max(1, (next.timestamp - prev.timestamp) / 1000);
  const distM = haversineMeters(prev.latitude, prev.longitude, next.latitude, next.longitude);
  const speedMps = distM / dtSec;
  const shortTeleport = dtSec < shortJumpWindowS && distM > shortJumpM;
  return {
    ok: speedMps <= maxSpeedMps && !shortTeleport,
    distM,
    speedMps,
    dtSec,
  };
}

/**
 * Filters noisy GPS fixes so one bad point can never rewrite a plan.
 * A jump is held as a suspect fix; only a second mutually consistent accurate fix
 * confirms a real relocation (e.g. after a tunnel or screen-locked stretch).
 */
export function createGpsGuard({
  maxAccuracyM = MAX_ACCURACY_M,
  maxSpeedMps = MAX_SPEED_MPS,
  shortJumpM = SHORT_JUMP_M,
  shortJumpWindowS = SHORT_JUMP_WINDOW_S,
} = {}) {
  let lastAccepted = null;
  let suspect = null;

  function evaluate(rawFix) {
    const latitude = Number(rawFix?.latitude);
    const longitude = Number(rawFix?.longitude);
    const accuracy = rawFix?.accuracy == null ? 0 : Number(rawFix.accuracy);
    const timestamp = Number.isFinite(Number(rawFix?.timestamp)) ? Number(rawFix.timestamp) : Date.now();

    if (
      !Number.isFinite(latitude)
      || !Number.isFinite(longitude)
      || Math.abs(latitude) > 90
      || Math.abs(longitude) > 180
    ) {
      return { accepted: false, reason: "invalid", accuracy };
    }
    if (!Number.isFinite(accuracy) || accuracy < 0 || accuracy > maxAccuracyM) {
      return { accepted: false, reason: "accuracy", accuracy };
    }

    const fix = { latitude, longitude, accuracy, timestamp };
    if (!lastAccepted) {
      lastAccepted = fix;
      suspect = null;
      return { accepted: true, reason: "first", movedM: 0, speedMps: 0, fix };
    }

    const step = isPlausibleStep(lastAccepted, fix, maxSpeedMps, shortJumpM, shortJumpWindowS);
    if (step.ok) {
      lastAccepted = fix;
      suspect = null;
      return { accepted: true, reason: "ok", movedM: step.distM, speedMps: step.speedMps, fix };
    }

    if (suspect) {
      const confirm = isPlausibleStep(suspect, fix, maxSpeedMps, shortJumpM, shortJumpWindowS);
      if (confirm.ok) {
        lastAccepted = fix;
        suspect = null;
        return { accepted: true, reason: "recovered", movedM: step.distM, speedMps: step.speedMps, fix };
      }
    }

    suspect = fix;
    return { accepted: false, reason: "jump", movedM: step.distM, speedMps: step.speedMps, fix };
  }

  function reset() {
    lastAccepted = null;
    suspect = null;
  }

  function stateSnapshot() {
    return { lastAccepted, suspect };
  }

  return { evaluate, reset, stateSnapshot };
}

/**
 * Adaptive polling & recalibration schedule.
 * Speeds up within 10 min of a stop, within 1 km of an aid station, or off-course.
 */
export function pollingPolicy({
  batterySaver = false,
  stopEtaMin = Infinity,
  aidDistanceM = Infinity,
  offCourseM = 0,
} = {}) {
  const nearStop = Number.isFinite(stopEtaMin) && stopEtaMin <= APPROACH_STOP_MIN;
  const nearAid = Number.isFinite(aidDistanceM) && aidDistanceM <= APPROACH_AID_M;
  const offCourse = Number.isFinite(offCourseM) && offCourseM > OFF_COURSE_M;
  const approach = nearStop || nearAid || offCourse;

  if (batterySaver) {
    return {
      mode: "poll",
      state: approach ? "approach" : "steady",
      pollIntervalMs: approach ? 20_000 : 60_000,
      processThrottleMs: approach ? 5_000 : 15_000,
      recalIntervalMs: approach ? 60_000 : 300_000,
      minMoveM: approach ? 20 : 50,
      forceRecalMs: 600_000,
    };
  }
  return {
    mode: "watch",
    state: approach ? "approach" : "steady",
    pollIntervalMs: approach ? 3_000 : 10_000,
    processThrottleMs: approach ? 3_000 : 10_000,
    recalIntervalMs: approach ? 30_000 : 120_000,
    minMoveM: approach ? 20 : 50,
    forceRecalMs: 300_000,
  };
}
