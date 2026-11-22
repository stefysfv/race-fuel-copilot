/** Screen Wake Lock helper only. All notifications, voice, and chimes are removed. */

import { setWakeLockState } from "./battery_diag.js";
import { state } from "./state.js";

export function bindLiveAlerts() {}
export function preloadEnglishVoices() {}
export function primeAudio() {}
export function playCountdownBeep() {}
export function announce() {}
export function raceCoachTick() {}
export function onAlertsClick() {}
export function onTestAlert() {}
export function alertsBlockedMessage() {
  return "";
}

export async function requestWakeLock() {
  if (!("wakeLock" in navigator) || state.wakeLock) return;
  try {
    state.wakeLock = await navigator.wakeLock.request("screen");
    setWakeLockState("active");
    state.wakeLock.addEventListener("release", () => {
      state.wakeLock = null;
      setWakeLockState("released");
    });
  } catch {
    setWakeLockState("unavailable");
  }
}

export async function releaseWakeLock() {
  if (!state.wakeLock) return;
  try {
    await state.wakeLock.release();
  } catch {
    /* ignore */
  } finally {
    state.wakeLock = null;
    setWakeLockState("released");
  }
}
