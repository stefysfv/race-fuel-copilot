/** Live tab: Minimal Light check-in guide with clock-time fueling windows & log-based ETA. */

import { state, ui } from "./state.js";
import {
  compactHours,
  elapsedMin,
  escapeHtml,
  formatClock,
  formatDuration,
  formatElapsedClock,
  grouped,
  setStatus,
} from "./ui_helpers.js";
import { currentSegment, nextAid, nextStop, shortIntake } from "./plan_cursor.js";
import { persist } from "./session.js";
import { formatDistance } from "./units.js";
import {
  buildConsumptionWindows,
  formatTimeOfDay,
  isFarOffCourse,
  legKey,
  projectLegs,
} from "./leg_projection.js";
import { drawMap } from "./map_screen.js";

let clockTimer = null;
let paintDeskHook = () => {};
let showAllWindows = false;
let lastWindowLegKey = "";
let windowsClickBound = false;

export function onPaintDesk(fn) {
  paintDeskHook = fn;
}

export function flavorTag(stop) {
  if (!stop) return "";
  if (stop.form === "micro_dose" || stop.form === "liquid") return "Liquid";
  if (stop.form === "rescue_sip" || (stop.form === "sip" && stop.carbs_g === 0)) return "Sip";
  if (stop.flavor === "Savoury/Salty") return "Savoury";
  if (stop.flavor) return stop.flavor;
  return "";
}

/** Runner state handed to the pure projection functions in leg_projection.js. */
function runnerState() {
  return {
    sectionLogs: state.sectionLogs,
    lastGpsRecalAt: state.lastGpsRecalAt,
    snap: state.snap,
    completedLegs: state.completedLegs,
    passedDistanceM: state.passedDistanceM,
    packedPlan: state.packedPlan,
    logged: state.logged,
    pausedAt: state.pausedAt,
    now: Date.now(),
  };
}

export function sectionForDistanceKm(distanceKm) {
  const legs = state.packedPlan?.aid_legs || [];
  if (!legs.length) return { index: 0, name: "Course" };
  const km = Number(distanceKm) || 0;
  const idx = legs.findIndex((leg) => km <= Number(leg.to_km) + 0.05);
  const chosen = idx >= 0 ? idx : legs.length - 1;
  const leg = legs[chosen];
  return { index: chosen, name: `${leg.from_name} → ${leg.to_name}` };
}

function renderLaunchScreen() {
  const plan = state.packedPlan;
  const started = Boolean(state.raceMode || state.startedAt || state.sim.active);
  ui.liveEmpty?.classList.toggle("hidden", Boolean(plan));
  ui.raceLaunch?.classList.toggle("hidden", !plan || started);
  ui.raceCockpit?.classList.toggle("hidden", !plan || !started);
  if (!plan) return;
  if (ui.raceLaunchTitle) {
    ui.raceLaunchTitle.textContent = plan.race_name || "Your race plan";
  }
  if (ui.raceLaunchMeta) {
    const legCount = plan.aid_legs?.length || 0;
    ui.raceLaunchMeta.textContent = `${formatDistance(plan.distance_km, { digits: 1 })} · ${formatDuration(plan.predicted_duration_min)} · ${legCount} ${legCount === 1 ? "section" : "sections"}`;
  }
  if (ui.raceLaunchEta) {
    const finishMs = Date.now() + (Number(plan.predicted_duration_min) || 0) * 60000;
    ui.raceLaunchEta.textContent = `Starting now puts finish around ${formatTimeOfDay(finishMs, 5)}`;
  }
}

export function renderLive() {
  renderLaunchScreen();
  paintRaceControls();
  const hasPlan = Boolean(state.livePlan);
  if (ui.log) ui.log.disabled = !hasPlan;
  if (ui.gps) ui.gps.disabled = !hasPlan;
  if (ui.rescue) ui.rescue.disabled = !hasPlan;
  const stop = nextStop();
  const elapsed = elapsedMin();
  const clockOn = Boolean(state.startedAt) || state.sim.active;
  paintCaffeine(elapsed);
  paintDeskHook();

  if (!hasPlan) {
    paintBadge(null);
    if (ui.nextAid) ui.nextAid.textContent = "—";
    paintRaceHud(0);
    renderAheadLegs(0);
    renderRaceLog();
    return;
  }

  const { ahead, done, pace } = projectLegs(state.livePlan, elapsed, runnerState());
  const currentEntry = ahead[0] || null;
  // Far from the course the planned grade, zone and arrival are not about you, so hide them.
  // Fueling windows below still use planned values; they are clock-based, not position-based.
  const offCourse = isFarOffCourse(state.offTrailM);
  ui.etaBlock?.classList.toggle("hidden", offCourse);
  ui.terrainStrip?.classList.toggle("hidden", offCourse);

  // Populate Hero Section Card (expected arrival + approximate consumption windows)
  if (currentEntry) {
    if (ui.currentSectionTitle) {
      ui.currentSectionTitle.textContent = offCourse
        ? "Off course"
        : `${currentEntry.leg.from_name} → ${currentEntry.leg.to_name}`;
    }
    if (ui.currentEtaClock) {
      ui.currentEtaClock.textContent = `~${formatTimeOfDay(currentEntry.etaClockMs, 5)}`;
    }
    if (ui.currentEtaSub) {
      const statusPrefix = state.pausedAt ? "Paused · " : "";
      ui.currentEtaSub.textContent = `${statusPrefix}Expected arrival in ${compactHours(currentEntry.remMin)} · ${pace.basis}`;
    }
    if (ui.sectionDoneCurrent) {
      ui.sectionDoneCurrent.classList.remove("hidden");
      ui.sectionDoneCurrent.dataset.legDone = currentEntry.key;
      ui.sectionDoneCurrent.dataset.legKm = String(currentEntry.leg.to_km || 0);
      ui.sectionDoneCurrent.dataset.legPlanMin = String(currentEntry.plannedCumMin || 0);
    }
    if (ui.sectionWindows) {
      if (lastWindowLegKey !== currentEntry.key) {
        lastWindowLegKey = currentEntry.key;
        showAllWindows = false;
      }
      if (!windowsClickBound) {
        windowsClickBound = true;
        ui.sectionWindows.addEventListener("click", (event) => {
          if (event.target.closest?.("[data-toggle-windows]")) {
            showAllWindows = !showAllWindows;
            renderLive();
          }
        });
      }
      const windows = buildConsumptionWindows(state.livePlan, currentEntry, elapsed, pace, runnerState());
      const limit = 3;
      const visible = showAllWindows || windows.length <= limit ? windows : windows.slice(0, limit);
      const hiddenCount = windows.length - visible.length;
      const rowsHtml = visible.map((row) => `
        <li class="window-row ${row.kind === "station" ? "is-station" : ""}" data-testid="fuel-window">
          <span class="window-time">${escapeHtml(row.time)}</span>
          <span class="window-items">${escapeHtml(row.text)}</span>
        </li>
      `).join("");
      const toggleHtml = windows.length > limit
        ? `<li class="window-toggle-row"><button type="button" class="text-btn windows-toggle" data-toggle-windows="1">${showAllWindows ? "Show fewer windows" : `Show all ${windows.length} windows (+${hiddenCount} more)`}</button></li>`
        : "";
      ui.sectionWindows.innerHTML = rowsHtml + toggleHtml;
    }
  } else {
    if (ui.currentSectionTitle) ui.currentSectionTitle.textContent = "All sections cleared";
    if (ui.currentEtaClock) ui.currentEtaClock.textContent = "Finish";
    if (ui.currentEtaSub) ui.currentEtaSub.textContent = "Open Finish Run in ••• for your full race recap";
    ui.sectionDoneCurrent?.classList.add("hidden");
    if (ui.sectionWindows) ui.sectionWindows.replaceChildren();
  }

  // Up next single compact line
  if (ui.countdown) {
    if (!stop) {
      ui.countdown.textContent = "Done";
    } else if (!clockOn) {
      ui.countdown.textContent = formatClock(stop.scheduled_minute);
    } else {
      const remaining = stop.scheduled_minute - elapsed;
      const stopClock = formatTimeOfDay((state.pausedAt || Date.now()) + Math.max(0, remaining) * 60000, 5);
      ui.countdown.textContent = remaining > 0 ? `~${stopClock}` : "Now";
    }
  }
  if (ui.intake) {
    ui.intake.textContent = stop ? shortIntake(stop) : "All fuel stops logged";
  }
  if (ui.caption) {
    ui.caption.textContent = stop ? flavorTag(stop) : "";
  }

  paintRaceHud(elapsed);
  renderAheadLegs(elapsed, { ahead, done });
  renderRaceLog();
  paintBadge(offCourse ? null : currentSegment());
  const aid = nextAid(clockOn ? elapsed : 0);
  if (ui.nextAid) {
    ui.nextAid.textContent = aid ? compactHours(aid.etaMin) : "—";
    ui.nextAid.title = aid ? `${aid.name} · ${formatDistance(aid.km, { digits: 1 })}` : "";
  }
}

export function paintBadge(segment) {
  if (!ui.badge || !ui.grade) return;
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
  if (!state.packedPlan || state.startedAt) return;
  state.startedAt = Date.now();
  state.pausedAt = null;
  state.pausedTotalMs = 0;
  if (!state.logged.length) state.intakeHistory = [];
  ensureTicker();
  renderLive();
  persist();
}

export function paintRaceHud(elapsed = elapsedMin()) {
  const active = Boolean(state.raceMode || state.startedAt);
  ui.raceHud?.classList.toggle("hidden", !active);
  if (!active) return;
  if (ui.raceLiveBadge) {
    ui.raceLiveBadge.classList.toggle("is-paused", Boolean(state.pausedAt));
    if (state.raceCountdown) {
      ui.raceLiveBadge.textContent = "STARTING";
    } else if (state.pausedAt) {
      ui.raceLiveBadge.textContent = "PAUSED";
    } else {
      ui.raceLiveBadge.textContent = "LIVE";
    }
  }
  if (ui.raceElapsed) {
    ui.raceElapsed.textContent = formatElapsedClock(elapsed);
  }
  if (ui.liveProgressLine && state.packedPlan) {
    const passedKm = (state.passedDistanceM || 0) / 1000;
    const remainingKm = Math.max(0, (state.packedPlan.distance_km || 0) - passedKm);
    ui.liveProgressLine.textContent = passedKm > 0.05
      ? `${formatDistance(passedKm, { digits: 1 })} passed · ${formatDistance(remainingKm, { digits: 1 })} left`
      : `${formatDistance(state.packedPlan.distance_km || 0, { digits: 1 })} remaining`;
  }
  if (ui.racePause) {
    ui.racePause.textContent = state.pausedAt ? "Resume race" : "Pause race";
    ui.racePause.setAttribute("aria-pressed", state.pausedAt ? "true" : "false");
  }
}

function summarizePackShort(pack = []) {
  return pack
    .map((item) => `${item.quantity}× ${String(item.item).replace("Soft flask 500 ml", "Flask").replace("Salt capsule", "Salt")}`)
    .join(" · ");
}

export function renderAheadLegs(elapsed = elapsedMin(), precomputed = null) {
  if (!ui.liveAheadSection || !ui.liveAheadList) return;
  const plan = state.livePlan;
  const active = Boolean(plan?.aid_legs?.length && (state.raceMode || state.startedAt));
  if (!active) {
    ui.liveAheadSection.classList.add("hidden");
    return;
  }

  const { ahead, done } = precomputed || projectLegs(plan, elapsed, runnerState());
  const upcomingLater = ahead.slice(1); // First leg is shown in the Hero Section Card

  ui.liveAheadSection.classList.toggle("hidden", upcomingLater.length === 0 && done.length === 0);
  ui.liveAheadList.replaceChildren();

  for (const item of upcomingLater) {
    const { leg, etaClockMs, scaledLegMin } = item;
    const row = document.createElement("article");
    row.className = "ahead-row";
    row.dataset.testid = "ahead-leg-card";
    const packSummary = summarizePackShort(leg.pack) || "Water sips";
    row.innerHTML = `
      <div class="ahead-row-top">
        <strong>${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}</strong>
        <span class="ahead-eta">~${escapeHtml(formatTimeOfDay(etaClockMs, 5))} · ${escapeHtml(compactHours(scaledLegMin))}</span>
      </div>
      <p class="ahead-row-meta">${grouped(leg.carbs_g)}g · ${escapeHtml(packSummary)}</p>
    `;
    ui.liveAheadList.append(row);
  }

  if (ui.liveCompletedDetails && ui.liveCompletedSummary && ui.liveCompletedList) {
    ui.liveCompletedDetails.classList.toggle("hidden", done.length === 0);
    ui.liveCompletedSummary.textContent = `Completed sections (${done.length})`;
    ui.liveCompletedList.replaceChildren();
    for (const item of done) {
      const li = document.createElement("li");
      li.className = "completed-leg-row";
      const logEntry = (state.sectionLogs || []).find((l) => l.key === item.key);
      const timeTag = logEntry ? ` · ${formatTimeOfDay(logEntry.clockMs, 1)}` : "";
      li.innerHTML = `<span>${escapeHtml(item.leg.from_name)} → ${escapeHtml(item.leg.to_name)}${escapeHtml(timeTag)}</span>`;
      if (!item.archivedByGps) {
        const undoBtn = document.createElement("button");
        undoBtn.type = "button";
        undoBtn.className = "text-btn";
        undoBtn.textContent = "Restore";
        undoBtn.addEventListener("click", () => reopenLegCompleted(item.key));
        li.append(undoBtn);
      }
      ui.liveCompletedList.append(li);
    }
  }
}

export async function markLegCompleted(key, toKm = 0, plannedCumMin = 0) {
  if (!key) return;
  if (!state.completedLegs.includes(key)) {
    state.completedLegs.push(key);
  }
  const actualElapsedMin = elapsedMin();
  const clockMs = state.pausedAt || Date.now();
  const existing = state.sectionLogs.findIndex((item) => item.key === key);
  const entry = {
    key,
    toKm: Number(toKm) || 0,
    plannedCumMin: Number(plannedCumMin) || 0,
    actualElapsedMin,
    clockMs,
  };
  if (existing >= 0) state.sectionLogs[existing] = entry;
  else state.sectionLogs.push(entry);
  if (entry.toKm > 0) {
    state.passedDistanceM = Math.max(state.passedDistanceM || 0, entry.toKm * 1000);
  }
  renderLive();
  drawMap();
  await persist();
}

export async function reopenLegCompleted(key) {
  state.completedLegs = (state.completedLegs || []).filter((item) => item !== key);
  state.sectionLogs = (state.sectionLogs || []).filter((item) => item.key !== key);
  renderLive();
  drawMap();
  await persist();
}

export function renderRaceLog() {
  if (!ui.raceLogCard || !ui.raceLogList) return;
  const rows = Array.isArray(state.intakeHistory) ? state.intakeHistory : [];
  const active = Boolean((state.raceMode || state.startedAt) && rows.length > 0);
  ui.raceLogCard.classList.toggle("hidden", !active);
  if (!active) return;
  if (ui.raceLogSummary) {
    const totalG = rows.reduce((sum, item) => sum + (Number(item.carbs_g) || 0), 0);
    ui.raceLogSummary.textContent = `Logged intakes (${rows.length} · ${totalG}g)`;
  }
  ui.raceLogList.replaceChildren();
  for (const item of rows.slice(0, 8)) {
    const li = document.createElement("li");
    li.className = "race-log-item";
    const timeStr = item.wallClock || item.clockText || "";
    li.textContent = `${timeStr} · ${item.label}`;
    ui.raceLogList.append(li);
  }
}

export function ensureTicker() {
  if (clockTimer || !state.startedAt) return;
  clockTimer = setInterval(() => {
    const liveOpen = !document.querySelector("#tab-live").classList.contains("hidden");
    if (liveOpen) renderLive();
  }, 1000);
}

export async function logIntake(options = {}) {
  const stop = nextStop();
  if (!stop) return;
  if (!state.startedAt) startClock();
  const elapsed = elapsedMin();
  const section = sectionForDistanceKm(stop.distance_km);
  const nowMs = state.pausedAt || Date.now();
  state.logged.push(stop.sequence);
  state.intakeHistory.unshift({
    sequence: stop.sequence,
    clockText: formatElapsedClock(elapsed),
    wallClock: formatTimeOfDay(nowMs, 1),
    elapsedMin: Number(elapsed.toFixed(2)),
    label: shortIntake(stop),
    carbs_g: Math.round(stop.carbs_g || 0),
    distance_km: Number(stop.distance_km || 0),
    sectionIndex: section.index,
    sectionName: section.name,
    auto: Boolean(options.auto),
  });
  paintRaceControls();
  renderLive();
  drawMap();
  await persist();
}

export function paintRaceControls() {
}

export function paintCaffeine(elapsed) {
  if (!ui.caffeine) return;
  const plan = state.packedPlan;
  if (!plan?.totals || !(plan.totals.caffeine_mg > 0)) {
    ui.caffeine.textContent = "";
    return;
  }
  const used = (state.livePlan?.fuel_stops || [])
    .filter((stop) => (stop.caffeine_mg || 0) > 0 && stop.scheduled_minute <= elapsed + 0.05)
    .reduce((sum, stop) => sum + stop.caffeine_mg, 0);
  ui.caffeine.textContent = `Caffeine ${Math.round(used)} / ${Math.round(plan.totals.caffeine_cap_mg || 0)} mg`;
}

export function openRaceMenu() {
  ui.raceMenuModal?.classList.remove("hidden");
}

export function closeRaceMenu() {
  ui.raceMenuModal?.classList.add("hidden");
}

export function openRecapModal() {
  closeRaceMenu();
  if (!ui.recapModal || !ui.recapBody || !state.packedPlan) return;
  const plan = state.packedPlan;
  const elapsed = elapsedMin();
  const planned = Number(plan.predicted_duration_min) || 0;
  const deltaMin = Math.round(elapsed - planned);
  const deltaLabel = elapsed < 1
    ? "Not timed"
    : (deltaMin === 0 ? "On plan" : (deltaMin > 0 ? `+${formatDuration(deltaMin)}` : `−${formatDuration(Math.abs(deltaMin))}`));
  const history = [...(state.intakeHistory || [])].reverse();
  const totalCarbs = history.reduce((sum, item) => sum + (Number(item.carbs_g) || 0), 0);
  const giEvents = state.giRescueEvents || [];
  const passedKm = (state.passedDistanceM || 0) / 1000;
  const completed = new Set(state.completedLegs || []);

  const sectionsHtml = (plan.aid_legs || []).map((leg, idx) => {
    const key = legKey(leg);
    const items = history.filter((entry) => entry.sectionIndex === idx);
    const legGi = giEvents.filter((ev) => ev.sectionIndex === idx);
    const legCarbs = items.reduce((sum, entry) => sum + (Number(entry.carbs_g) || 0), 0);
    const logRow = (state.sectionLogs || []).find((l) => l.key === key);
    const isPassed = completed.has(key) || (passedKm > 0.1 && Number(leg.to_km) <= passedKm + 0.05);
    const statusText = logRow ? `Cleared ${formatTimeOfDay(logRow.clockMs, 1)}` : (isPassed ? "Passed" : "Active");
    const listHtml = items.length
      ? `<ul class="recap-items">${items.map((entry) => `<li><strong>${escapeHtml(entry.wallClock || entry.clockText || "")}</strong> · ${escapeHtml(entry.label)}</li>`).join("")}</ul>`
      : `<p class="micro">No intakes logged on this section.</p>`;
    const giHtml = legGi.length
      ? `<p class="recap-gi-flag">GI rescue (${legGi.map((g) => escapeHtml(g.clockText)).join(", ")})</p>`
      : "";
    return `
      <section class="recap-leg" data-testid="recap-leg">
        <div class="recap-leg-head">
          <strong>${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}</strong>
          <span class="time-badge">${escapeHtml(statusText)} · ${legCarbs}g / ${Math.round(leg.carbs_g)}g</span>
        </div>
        ${listHtml}
        ${giHtml}
      </section>
    `;
  }).join("");

  ui.recapBody.innerHTML = `
    <div class="recap-summary" data-testid="recap-summary">
      <h3>${escapeHtml(plan.race_name)}</h3>
      <div class="macros">
        <div><strong>${escapeHtml(formatElapsedClock(elapsed))}</strong><span>Elapsed (${escapeHtml(deltaLabel)})</span></div>
        <div><strong>${totalCarbs}g</strong><span>${history.length} logged</span></div>
        <div><strong>${giEvents.length}</strong><span>GI rescues</span></div>
      </div>
    </div>
    <p class="kicker">Section-by-section intake recap</p>
    <div class="recap-sections">${sectionsHtml}</div>
  `;
  ui.recapModal.classList.remove("hidden");
}

export function closeRecapModal() {
  ui.recapModal?.classList.add("hidden");
}

export function prefillFeedbackFromRecap() {
  const plan = state.packedPlan;
  if (!plan) return;
  const elapsed = elapsedMin();
  const raceInput = document.querySelector("#feedback-race");
  const finishInput = document.querySelector("#feedback-finish");
  const notesInput = document.querySelector("#feedback-notes");
  if (raceInput) raceInput.value = plan.race_name || "";
  if (finishInput && elapsed >= 0.5) {
    finishInput.value = `${formatDuration(elapsed)} vs ${formatDuration(plan.predicted_duration_min)} predicted`;
  }
  const giCount = (state.giRescueEvents || []).length;
  const giVal = giCount >= 2 ? "severe" : (giCount === 1 || state.giRescue ? "mild" : "none");
  for (const radio of document.querySelectorAll("input[name='feedback-gi']")) {
    radio.checked = radio.value === giVal;
  }
  if (notesInput && !notesInput.value.trim() && (state.intakeHistory || []).length) {
    const lines = [...state.intakeHistory].reverse().map(
      (item) => `${item.wallClock || item.clockText} [${item.sectionName}]: ${item.label}`,
    );
    notesInput.value = `Logged intakes:\n${lines.join("\n")}`;
  }
  raceInput?.dispatchEvent(new Event("input", { bubbles: true }));
}
