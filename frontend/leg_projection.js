/** Leg projection for the Live check-in screen: pace from your log, arrival times, fuel windows.
 *
 * Pure functions with no DOM or state.js imports, so `node --test` can load them.
 * Callers pass the runner state as `run`:
 *   { sectionLogs, lastGpsRecalAt, snap, completedLegs, passedDistanceM, packedPlan,
 *     logged, pausedAt, now }
 * `now` (ms) is optional and only used when the race is not paused; it defaults to Date.now().
 */

/** A log-based pace ratio is clamped to this range so one bad check-in cannot wreck the ETAs. */
export const PACE_RATIO_MIN = 0.7;
export const PACE_RATIO_MAX = 1.85;

export function legKey(leg) {
  return `${leg.from_name}->${leg.to_name}@${Number(leg.to_km || 0).toFixed(2)}`;
}

export function formatTimeOfDay(ms, roundStepMin = 1) {
  const stepMs = Math.max(1, roundStepMin) * 60000;
  const rounded = Math.round(ms / stepMs) * stepMs;
  const d = new Date(rounded);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

/** Past this distance from the course, planned grade, zone and arrival times mean nothing. */
export const OFF_COURSE_HIDE_M = 2000;

export function isFarOffCourse(offCourseM) {
  return Number.isFinite(offCourseM) && offCourseM > OFF_COURSE_HIDE_M;
}

function clockNow(run) {
  if (!run.startedAt && Number.isFinite(run.plannedStartMs)) {
    return run.plannedStartMs;
  }
  return run.pausedAt || run.now || Date.now();
}

/**
 * Computes pace multiplier & anchor time from either GPS recalibration or
 * the runner's latest manual section check-in log.
 */
export function computePaceContext(plan, elapsed, run) {
  const latestLog = (run.sectionLogs || []).slice(-1)[0] || null;
  const gpsFresh = Boolean(run.lastGpsRecalAt && (!latestLog || run.lastGpsRecalAt >= latestLog.clockMs));
  if (gpsFresh && run.snap) {
    return {
      ratio: 1,
      basis: "based on GPS check-in",
      startClockMin: Number(plan?.clock_start_min) || 0,
      anchorElapsedMin: elapsed,
      anchorAtLegEnd: null,
    };
  }
  if (latestLog && latestLog.plannedCumMin > 2 && latestLog.actualElapsedMin > 0.5) {
    const ratio = Math.min(PACE_RATIO_MAX, Math.max(PACE_RATIO_MIN, latestLog.actualElapsedMin / latestLog.plannedCumMin));
    const pct = Math.round((ratio - 1) * 100);
    const basis = Math.abs(pct) <= 3
      ? "on pace from your log"
      : (pct > 0 ? `+${pct}% pace from your log` : `${pct}% pace from your log`);
    return {
      ratio,
      basis,
      startClockMin: Number(plan?.clock_start_min) || 0,
      anchorElapsedMin: Math.max(elapsed, latestLog.actualElapsedMin),
      anchorAtLegEnd: latestLog.key,
    };
  }
  return {
    ratio: 1,
    basis: "planned pace",
    startClockMin: Number(plan?.clock_start_min) || 0,
    anchorElapsedMin: elapsed,
    anchorAtLegEnd: null,
  };
}

/**
 * Builds remaining & completed legs with projected wall-clock arrival times.
 */
export function projectLegs(plan, elapsed, run) {
  if (!plan?.aid_legs?.length) return { ahead: [], done: [], pace: computePaceContext(plan, elapsed, run) };
  const pace = computePaceContext(plan, elapsed, run);
  const completed = new Set(run.completedLegs || []);
  const passedKm = (run.passedDistanceM || 0) / 1000;
  const nowMs = clockNow(run);

  let plannedCursor = pace.startClockMin;
  let projectedCursor = pace.startClockMin;
  const ahead = [];
  const done = [];

  for (const leg of plan.aid_legs) {
    const key = legKey(leg);
    const legMin = Number(leg.predicted_minutes) || 0;
    const legStartPlan = plannedCursor;
    plannedCursor += legMin;

    const isPassed = completed.has(key) || (passedKm > 0.1 && Number(leg.to_km) <= passedKm - 0.05);
    if (isPassed) {
      if (pace.anchorAtLegEnd && key === pace.anchorAtLegEnd) {
        projectedCursor = pace.anchorElapsedMin;
      } else {
        projectedCursor = Math.max(projectedCursor + legMin * pace.ratio, elapsed);
      }
      done.push({ leg, key, etaClockMs: null, plannedCumMin: plannedCursor });
      continue;
    }

    const startElapsed = Math.max(projectedCursor, elapsed);
    const scaledLegMin = legMin * pace.ratio;
    const etaElapsedMin = startElapsed + scaledLegMin;
    projectedCursor = etaElapsedMin;
    const remMin = Math.max(0, etaElapsedMin - elapsed);

    ahead.push({
      leg,
      key,
      legStartPlan,
      legEndPlan: plannedCursor,
      startClockMs: nowMs + Math.max(0, startElapsed - elapsed) * 60000,
      etaClockMs: nowMs + remMin * 60000,
      etaElapsedMin,
      remMin,
      scaledLegMin,
      plannedCumMin: plannedCursor,
    });
  }

  // Include original legs dropped after GPS recalibration in the completed archive
  const liveKeys = new Set(plan.aid_legs.map((leg) => legKey(leg)));
  for (const orig of run.packedPlan?.aid_legs || []) {
    const key = legKey(orig);
    if (!liveKeys.has(key) && (completed.has(key) || Number(orig.to_km) <= passedKm + 0.1)) {
      done.push({ leg: orig, key, etaClockMs: null, archivedByGps: true });
    }
  }

  return { ahead, done, pace };
}

function cleanStopName(stop) {
  if (stop.form === "rescue_sip" || (stop.form === "sip" && stop.carbs_g === 0)) {
    return stop.form === "sip" && Number(stop.sodium_mg) === 0 ? "Water sip" : "Water & electrolyte sip";
  }
  const name = stop.product || (stop.form === "liquid" || stop.form === "micro_dose" ? "Liquid carbs" : "Fuel");
  return String(name).replace(/soft flask.*/i, "Carb flask");
}

function summarizeWindowStops(stops) {
  const counts = new Map();
  for (const stop of stops) {
    const label = cleanStopName(stop);
    const prev = counts.get(label) || { count: 0, carbs: 0 };
    prev.count += 1;
    prev.carbs += Math.round(Number(stop.carbs_g) || 0);
    counts.set(label, prev);
  }
  return [...counts.entries()]
    .map(([label, info]) => {
      const qty = info.count > 1 ? `${info.count}× ` : "1× ";
      const grams = info.carbs > 0 ? ` (${info.carbs}g)` : "";
      return `${qty}${label}${grams}`;
    })
    .join(", ");
}

/**
 * Groups upcoming unlogged stops in the current section into clean ~1-hour wall-clock windows
 * (e.g. "12:30 – 13:30: 2× Standard Gel (50g), 1× Drink Mix (30g)").
 */
export function buildConsumptionWindows(plan, currentEntry, elapsed, pace, run) {
  if (!plan?.fuel_stops?.length || !currentEntry) return [];
  const nowMs = clockNow(run);
  const leg = currentEntry.leg;
  const fromKm = Number(leg.from_km) || 0;
  const toKm = Number(leg.to_km) || 0;

  const stops = plan.fuel_stops.filter((stop) => {
    if ((run.logged || []).includes(stop.sequence)) return false;
    if (stop.at_aid_station) return false;
    const km = Number(stop.distance_km) || 0;
    return km >= fromKm - 0.1 && km <= toKm + 0.05;
  });

  const rows = [];
  if (stops.length) {
    const timed = stops.map((stop) => {
      const offsetPlan = Math.max(0, Number(stop.scheduled_minute || 0) - currentEntry.legStartPlan);
      const stopMs = currentEntry.startClockMs + offsetPlan * pace.ratio * 60000;
      return { stop, stopMs: Math.max(nowMs, stopMs) };
    });

    const buckets = [];
    for (const item of timed) {
      const last = buckets[buckets.length - 1];
      if (!last || item.stopMs - last.startMs > 58 * 60000) {
        buckets.push({ startMs: item.stopMs, endMs: item.stopMs, items: [item.stop] });
      } else {
        last.endMs = item.stopMs;
        last.items.push(item.stop);
      }
    }

    for (const bucket of buckets) {
      let winStart = bucket.startMs;
      let winEnd = bucket.endMs;
      if (winEnd - winStart < 25 * 60000) {
        // Give a clear ~30–60 min consumption window capped at station arrival
        winStart = Math.max(nowMs, bucket.startMs - 10 * 60000);
        winEnd = Math.min(currentEntry.etaClockMs, Math.max(bucket.endMs + 20 * 60000, winStart + 30 * 60000));
      } else {
        winEnd = Math.min(currentEntry.etaClockMs, Math.max(winEnd + 10 * 60000, winStart + 60 * 60000));
      }
      const startLabel = formatTimeOfDay(winStart, 5);
      const endLabel = formatTimeOfDay(winEnd, 5);
      const timeLabel = startLabel === endLabel ? `~${startLabel}` : `${startLabel} – ${endLabel}`;
      rows.push({
        time: timeLabel,
        text: summarizeWindowStops(bucket.items),
        kind: "trail",
      });
    }
  } else {
    rows.push({
      time: `${formatTimeOfDay(nowMs, 5)} – ${formatTimeOfDay(currentEntry.etaClockMs, 5)}`,
      text: "Sip water & electrolytes to thirst",
      kind: "trail",
    });
  }

  if (leg.station_meal) {
    const mealLabel = leg.station_meal.startsWith("Aid Station Plate")
      ? `Potatoes / banana + cola (${Math.round(leg.station_carbs_g)}g)`
      : leg.station_meal;
    rows.push({
      time: `At ${leg.to_name} (~${formatTimeOfDay(currentEntry.etaClockMs, 5)})`,
      text: mealLabel,
      kind: "station",
    });
  }

  return rows;
}
