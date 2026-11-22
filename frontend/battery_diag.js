/** Check-in telemetry exposed in ?diag=1. A "fix" here is one accepted Sync GPS tap. */

const MAX_INTERVAL_SAMPLES = 60;

const metrics = {
  startedAt: Date.now(),
  gpsFixes: 0,
  gpsRejectedAccuracy: 0,
  gpsRejectedJump: 0,
  fixIntervalsMs: [],
  lastFixAt: null,
  lastIntervalMs: null,
  recalibrations: 0,
  backgroundResumes: 0,
  batterySaver: false,
};

export function recordGpsAccepted(timestamp = Date.now()) {
  if (metrics.lastFixAt != null && timestamp >= metrics.lastFixAt) {
    const gap = timestamp - metrics.lastFixAt;
    metrics.lastIntervalMs = gap;
    metrics.fixIntervalsMs.push(gap);
    if (metrics.fixIntervalsMs.length > MAX_INTERVAL_SAMPLES) {
      metrics.fixIntervalsMs.shift();
    }
  }
  metrics.lastFixAt = timestamp;
  metrics.gpsFixes += 1;
}

export function recordGpsRejected(reason) {
  if (reason === "accuracy") metrics.gpsRejectedAccuracy += 1;
  else if (reason === "jump") metrics.gpsRejectedJump += 1;
}

export function recordRecalibration() {
  metrics.recalibrations += 1;
}

export function recordBackgroundResume() {
  metrics.backgroundResumes += 1;
}

export function setBatterySaverDiag(enabled) {
  metrics.batterySaver = Boolean(enabled);
}

function medianMs(samples) {
  if (!samples.length) return null;
  const sorted = [...samples].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
}

export function batteryDiagSnapshot(now = Date.now()) {
  const elapsedHours = Math.max(1 / 60, (now - metrics.startedAt) / 3_600_000);
  const fixesPerHour = Math.round(metrics.gpsFixes / elapsedHours);
  const recalsPerHour = Math.round(metrics.recalibrations / elapsedHours);
  // Rough weighted work units per hour (1 per check-in, 5 per worker recalibration).
  const workScorePerHour = Math.round((metrics.gpsFixes + metrics.recalibrations * 5) / elapsedHours);
  const loadBand = workScorePerHour < 180 ? "low" : (workScorePerHour < 600 ? "moderate" : "high");
  return {
    uptimeMin: Math.round((now - metrics.startedAt) / 60_000),
    gpsFixes: metrics.gpsFixes,
    gpsRejectedAccuracy: metrics.gpsRejectedAccuracy,
    gpsRejectedJump: metrics.gpsRejectedJump,
    lastFixIntervalMs: metrics.lastIntervalMs,
    medianFixIntervalMs: medianMs(metrics.fixIntervalsMs),
    recalibrations: metrics.recalibrations,
    backgroundResumes: metrics.backgroundResumes,
    batterySaver: metrics.batterySaver,
    fixesPerHour,
    recalsPerHour,
    workScorePerHour,
    workSummary: `~${workScorePerHour} units/h (${loadBand}: ${fixesPerHour} check-ins, ${recalsPerHour} recals)`,
  };
}

export function resetBatteryDiag() {
  metrics.startedAt = Date.now();
  metrics.gpsFixes = 0;
  metrics.gpsRejectedAccuracy = 0;
  metrics.gpsRejectedJump = 0;
  metrics.fixIntervalsMs = [];
  metrics.lastFixAt = null;
  metrics.lastIntervalMs = null;
  metrics.recalibrations = 0;
  metrics.backgroundResumes = 0;
}
