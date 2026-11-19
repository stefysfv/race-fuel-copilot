/** Live tab: countdown, intake text, intake log, and the race clock. */

import { state, ui } from "./state.js";
import { compactHours, elapsedMin, formatClock, setStatus } from "./ui_helpers.js";
import { currentSegment, nextAid, nextStop, shortIntake } from "./plan_cursor.js";
import { persist } from "./session.js";

let clockTimer = null;
let clockTick = () => {};
let paintDeskHook = () => {};

/** Called once a second while the race clock is running. */
export function onClockTick(fn) {
  clockTick = fn;
}

/** Paints the desk simulator readout. */
export function onPaintDesk(fn) {
  paintDeskHook = fn;
}

export function flavorTag(stop) {
  if (!stop) return "";
  if (stop.form === "micro_dose" || stop.form === "liquid") return "Liquid Micro-Dose";
  if (stop.form === "rescue_sip" || (stop.form === "sip" && stop.carbs_g === 0)) return "Sip";
  if (stop.flavor === "Savoury/Salty") return "Savoury";
  if (stop.flavor) return stop.flavor;
  if (stop.form === "rescue_sip" || stop.form === "sip") return "Sip";
  return "";
}

export function renderLive() {
  paintRaceControls();
  const hasPlan = Boolean(state.livePlan);
  ui.log.disabled = !hasPlan;
  ui.gps.disabled = !hasPlan;
  ui.rescue.disabled = !hasPlan;
  const stop = nextStop();
  const elapsed = elapsedMin();
  const clockOn = Boolean(state.startedAt) || state.sim.active;
  paintCaffeine(elapsed);
  paintDeskHook();
  if (!hasPlan) {
    ui.countdown.classList.remove("is-complete");
    ui.countdown.textContent = "--:--";
    ui.caption.textContent = "";
    ui.intake.textContent = "Build a plan";
    paintBadge(null);
    ui.nextAid.textContent = "—";
    return;
  }
  if (!stop) {
    ui.countdown.classList.add("is-complete");
    ui.countdown.textContent = "Race Fueling Complete";
    ui.caption.textContent = "";
    ui.intake.textContent = "Maintain hydration sips to the finish line";
  } else if (!clockOn) {
    ui.countdown.classList.remove("is-complete");
    ui.countdown.textContent = formatClock(stop.scheduled_minute);
    ui.caption.textContent = flavorTag(stop) || "Tap clock";
    ui.intake.textContent = shortIntake(stop);
  } else {
    ui.countdown.classList.remove("is-complete");
    const remaining = stop.scheduled_minute - elapsed;
    ui.countdown.textContent = remaining > 0 ? formatClock(remaining) : "Due";
    ui.caption.textContent = flavorTag(stop);
    ui.intake.textContent = shortIntake(stop);
  }
  paintBadge(currentSegment());
  const aid = nextAid(clockOn ? elapsed : 0);
  ui.nextAid.textContent = aid ? compactHours(aid.etaMin) : "—";
  ui.nextAid.title = aid ? `${aid.name} · ${aid.km.toFixed(1)} km` : "";
}

export function paintBadge(segment) {
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

export function startClock() {
  if (!state.packedPlan || state.startedAt) {
    return;
  }
  state.startedAt = Date.now();
  ensureTicker();
  renderLive();
  persist();
}

export function ensureTicker() {
  if (clockTimer || !state.startedAt) {
    return;
  }
  clockTimer = setInterval(() => {
    const liveOpen = !document.querySelector("#tab-live").classList.contains("hidden");
    if (liveOpen) {
      renderLive();
    }
    clockTick();
  }, 1000);
}

export async function logIntake(options = {}) {
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

export function undoAutoLog() {
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

export function paintRaceControls() {
  ui.raceMode?.setAttribute("aria-pressed", state.raceMode ? "true" : "false");
  if (ui.raceMode) ui.raceMode.textContent = "Race Mode";
  ui.alerts?.setAttribute("aria-pressed", state.alerts ? "true" : "false");
  ui.undoAuto?.classList.toggle("hidden", state.lastAutoLog == null);
}

export function paintCaffeine(elapsed) {
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
