/** Voice coach, lock-screen alerts, and the screen wake lock. */

import { nextStop, shortIntake } from "./plan_cursor.js";
import { state } from "./state.js";
import { elapsedMin, setStatus } from "./ui_helpers.js";
import { recordNotification, recordSpeech, setWakeLockState } from "./battery_diag.js";

let logIntake = async () => {};
let paintRaceControls = () => {};

/** Live-tab actions the coach calls. Wired from app.js so alerts does not import live.js. */
export function bindLiveAlerts(log, paint) {
  logIntake = log;
  paintRaceControls = paint;
}

export function raceCoachTick() {
  if (!state.startedAt || state.sim.active || !state.livePlan || state.catchingUp) {
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

export function itemTitle(stop) {
  if (stop.at_aid_station) return "Aid plate";
  if (stop.form === "micro_dose" || stop.form === "liquid") return "Liquid";
  if (stop.carbs_g === 0) return "Sip";
  return stop.product || "Gel";
}

export function coachSpeech(stop) {
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

export function primeAudio() {
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
    if (applyEnglishVoice(unlock)) {
      window.speechSynthesis.speak(unlock);
    }
  }
  return state.audioCtx;
}

export function playChime() {
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

export const FEMALE_VOICE_NAMES = [
  "Ava",
  "Samantha",
  "Allison",
  "Victoria",
  "Karen",
  "Moira",
  "Serena",
  "Tessa",
  "Zira",
  "Aria",
  "Jenny",
  "Sonia",
  "Libby",
  "Google UK English Female",
  "Google US English",
  "Female",
];

export function preloadEnglishVoices() {
  const synth = window.speechSynthesis;
  if (!synth) return;
  synth.getVoices();
  synth.onvoiceschanged = () => {
    synth.getVoices();
  };
}

export function getEnglishFemaleVoice() {
  const synth = window.speechSynthesis;
  if (!synth) return null;
  const voices = synth.getVoices() || [];
  const english = voices.filter((voice) => String(voice.lang || "").toLowerCase().startsWith("en"));
  for (const name of FEMALE_VOICE_NAMES) {
    const needle = name.toLowerCase();
    const match = english.find((voice) => voice.name.toLowerCase().includes(needle));
    if (match) return match;
  }
  return english.find((voice) => voice.lang.toLowerCase().startsWith("en-us"))
    || english.find((voice) => voice.lang.toLowerCase().startsWith("en-gb"))
    || english[0]
    || null;
}

export function applyEnglishVoice(utterance) {
  const voice = getEnglishFemaleVoice();
  if (!voice || !String(voice.lang || "").toLowerCase().startsWith("en")) {
    return false;
  }
  utterance.voice = voice;
  utterance.lang = voice.lang;
  const named = FEMALE_VOICE_NAMES.some((name) => voice.name.toLowerCase().includes(name.toLowerCase()));
  if (!named) utterance.pitch = 1.12;
  return true;
}

export function sanitizeSpeech(text) {
  return String(text)
    .replace(/×/g, " ")
    .replace(/±/g, " ")
    .replace(/−/g, "-")
    .replace(/(^|[\s(])-(\d)/g, "$1minus $2")
    .replace(/(^|[\s(])\+(\d)/g, "$1plus $2")
    .replace(/[^\x00-\x7F]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function speak(text) {
  if (!window.speechSynthesis || !text) {
    return;
  }
  const spoken = sanitizeSpeech(text);
  if (!spoken) return;
  window.speechSynthesis.cancel();
  const utter = new SpeechSynthesisUtterance(spoken);
  utter.rate = 1;
  if (!applyEnglishVoice(utter)) return;
  recordSpeech();
  window.speechSynthesis.speak(utter);
}

export function announce(note) {
  playChime();
  window.setTimeout(() => speak(note.speech), 400);
  pushNote(note.title, note.body, note.tag);
}

export async function pushNote(title, body, tag) {
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
    recordNotification();
  } catch (error) {
    try {
      new Notification(title, options);
      recordNotification();
    } catch (fallbackError) {
      setStatus("The alert could not be posted to the lock screen.");
    }
  }
}

export async function ensureNotifyPermission() {
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

export function alertsBlockedMessage() {
  const standalone = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (!standalone) {
    return "On iPhone, add this app to the Home Screen, then enable alerts so they reach the lock screen.";
  }
  return "Notifications are blocked. Allow them in iPhone Settings > Notifications.";
}

export async function onAlertsClick() {
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

export async function onTestAlert() {
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

export async function requestWakeLock() {
  if (!navigator.wakeLock) {
    setWakeLockState("unsupported");
    return;
  }
  if (!state.raceMode) {
    return;
  }
  if (state.wakeLock) {
    setWakeLockState("active");
    return;
  }
  try {
    const sentinel = await navigator.wakeLock.request("screen");
    state.wakeLock = sentinel;
    setWakeLockState("active");
    sentinel.addEventListener?.("release", () => {
      if (state.wakeLock === sentinel) {
        state.wakeLock = null;
      }
      setWakeLockState("released");
    });
  } catch (error) {
    setWakeLockState("denied");
    if (!state.wakeWarned) {
      state.wakeWarned = true;
      setStatus("The screen may still sleep. The browser did not grant a wake lock.");
    }
  }
}

export function releaseWakeLock() {
  const lock = state.wakeLock;
  state.wakeLock = null;
  if (lock) {
    lock.release().catch(() => {});
    setWakeLockState("released");
  } else if (navigator.wakeLock) {
    setWakeLockState("inactive");
  } else {
    setWakeLockState("unsupported");
  }
}
