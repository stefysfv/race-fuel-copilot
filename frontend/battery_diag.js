/** Battery & race-mode telemetry exposed in ?diag=1. */

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
  speechCount: 0,
  notificationCount: 0,
  backgroundResumes: 0,
  wakeLockState: typeof navigator !== "undefined" && "wakeLock" in navigator ? "inactive" : "unsupported",
  pollingState: "steady",
  pollingMode: "watch",
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

export function recordSpeech() {
  metrics.speechCount += 1;
}

export function recordNotification() {
  metrics.notificationCount += 1;
}

export function recordBackgroundResume() {
  metrics.backgroundResumes += 1;
}

export function setWakeLockState(nextState) {
  metrics.wakeLockState = String(nextState || "inactive");
}

export function setPollingDiag({ state, mode, batterySaver }) {
  if (state) metrics.pollingState = state;
  if (mode) metrics.pollingMode = mode;
  if (typeof batterySaver === "boolean") metrics.batterySaver = batterySaver;
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
  const cuesPerHour = Math.round((metrics.speechCount + metrics.notificationCount) / elapsedHours);
  // Rough weighted work units per hour (1 per fix, 5 per worker recalibration, 2 per voice/notification).
  const workScorePerHour = Math.round(
    (metrics.gpsFixes + metrics.recalibrations * 5 + (metrics.speechCount + metrics.notificationCount) * 2)
      / elapsedHours,
  );
  const loadBand = workScorePerHour < 180 ? "low" : (workScorePerHour < 600 ? "moderate" : "high");
  return {
    uptimeMin: Math.round((now - metrics.startedAt) / 60_000),
    gpsFixes: metrics.gpsFixes,
    gpsRejectedAccuracy: metrics.gpsRejectedAccuracy,
    gpsRejectedJump: metrics.gpsRejectedJump,
    lastFixIntervalMs: metrics.lastIntervalMs,
    medianFixIntervalMs: medianMs(metrics.fixIntervalsMs),
    recalibrations: metrics.recalibrations,
    speechCount: metrics.speechCount,
    notificationCount: metrics.notificationCount,
    backgroundResumes: metrics.backgroundResumes,
    wakeLockState: metrics.wakeLockState,
    pollingState: metrics.pollingState,
    pollingMode: metrics.pollingMode,
    batterySaver: metrics.batterySaver,
    fixesPerHour,
    recalsPerHour,
    cuesPerHour,
    workScorePerHour,
    workSummary: `~${workScorePerHour} units/h (${loadBand}: ${fixesPerHour} fixes, ${recalsPerHour} recals, ${cuesPerHour} cues)`,
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
  metrics.speechCount = 0;
  metrics.notificationCount = 0;
  metrics.backgroundResumes = 0;
}
