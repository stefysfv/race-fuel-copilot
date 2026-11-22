/** Live tab: Launch card -> Active check-in cockpit, forward-only legs, intake log, and Race Recap. */

import { state, ui } from "./state.js";
import { compactHours, elapsedMin, escapeHtml, formatClock, formatDuration, formatElapsedClock, grouped, setStatus } from "./ui_helpers.js";
import { currentSegment, nextAid, nextStop, shortIntake } from "./plan_cursor.js";
import { persist } from "./session.js";
import { formatDistance } from "./units.js";
import { legKey } from "./crew_sheet.js";
import { drawMap } from "./map_screen.js";

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
    ui.raceLaunchMeta.textContent = `${formatDistance(plan.distance_km, { digits: 1 })} · Plan ${formatDuration(plan.predicted_duration_min)} · ${legCount} ${legCount === 1 ? "section" : "sections"}`;
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
    if (ui.countdown) {
      ui.countdown.classList.remove("is-complete");
      ui.countdown.textContent = "--:--";
    }
    if (ui.caption) ui.caption.textContent = "";
    if (ui.intake) ui.intake.textContent = "Build a plan";
    paintBadge(null);
    if (ui.nextAid) ui.nextAid.textContent = "—";
    paintRaceHud(0);
    renderAheadLegs();
    renderRaceLog();
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
    const tag = flavorTag(stop);
    ui.caption.textContent = state.pausedAt ? (tag ? `Paused · ${tag}` : "Paused") : tag;
    ui.intake.textContent = shortIntake(stop);
  }
  paintRaceHud(elapsed);
  renderAheadLegs();
  renderRaceLog();
  paintBadge(currentSegment());
  const aid = nextAid(clockOn ? elapsed : 0);
  ui.nextAid.textContent = aid ? compactHours(aid.etaMin) : "—";
  ui.nextAid.title = aid ? `${aid.name} · ${formatDistance(aid.km, { digits: 1 })}` : "";
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
  ui.raceSessionControls?.classList.toggle("hidden", !active);
  if (!active) return;
  if (ui.raceLiveBadge) {
    ui.raceLiveBadge.classList.toggle("is-paused", Boolean(state.pausedAt));
    if (state.raceCountdown) {
      ui.raceLiveBadge.textContent = "STARTING...";
    } else if (state.pausedAt) {
      ui.raceLiveBadge.textContent = "PAUSED";
    } else if (state.raceMode) {
      ui.raceLiveBadge.textContent = "RACE LIVE";
    } else {
      ui.raceLiveBadge.textContent = "TIMER ON";
    }
  }
  if (ui.raceElapsed) {
    ui.raceElapsed.textContent = formatElapsedClock(elapsed);
  }
  if (ui.liveProgressLine && state.packedPlan) {
    const passedKm = (state.passedDistanceM || 0) / 1000;
    const remainingKm = Math.max(0, (state.packedPlan.distance_km || 0) - passedKm);
    ui.liveProgressLine.textContent = passedKm > 0.05
      ? `${formatDistance(passedKm, { digits: 1 })} passed · ${formatDistance(remainingKm, { digits: 1 })} ahead`
      : `${formatDistance(state.packedPlan.distance_km || 0, { digits: 1 })} ahead · Sync GPS anytime to update ETAs`;
  }
  if (ui.racePause) {
    ui.racePause.textContent = state.pausedAt ? "Resume" : "Pause";
    ui.racePause.setAttribute("aria-pressed", state.pausedAt ? "true" : "false");
  }
}

function summarizePackShort(pack = []) {
  return pack
    .map((item) => `${item.quantity}× ${String(item.item).replace("Soft flask 500 ml", "500ml flask").replace("Salt capsule", "Salt cap")}`)
    .join(" · ");
}

export function renderAheadLegs() {
  if (!ui.liveAheadSection || !ui.liveAheadList) return;
  const plan = state.livePlan;
  const active = Boolean(plan?.aid_legs?.length && (state.raceMode || state.startedAt));
  ui.liveAheadSection.classList.toggle("hidden", !active);
  if (!active) return;

  const completed = new Set(state.completedLegs || []);
  const passedKm = (state.passedDistanceM || 0) / 1000;
  let cursor = Number(plan.clock_start_min) || 0;
  const ahead = [];
  const done = [];

  for (const leg of plan.aid_legs) {
    cursor += Number(leg.predicted_minutes) || 0;
    const key = legKey(leg);
    const isPassed = completed.has(key) || (passedKm > 0.1 && Number(leg.to_km) <= passedKm - 0.05);
    const entry = { leg, key, etaMin: cursor };
    if (isPassed) done.push(entry);
    else ahead.push(entry);
  }

  // Also count legs from the original packedPlan that were already dropped by GPS recalibration
  const liveKeys = new Set(plan.aid_legs.map((leg) => legKey(leg)));
  for (const orig of state.packedPlan?.aid_legs || []) {
    const key = legKey(orig);
    if (!liveKeys.has(key) && (completed.has(key) || Number(orig.to_km) <= passedKm + 0.1)) {
      done.push({ leg: orig, key, etaMin: null, archivedByGps: true });
    }
  }

  ui.liveAheadList.replaceChildren();
  if (!ahead.length) {
    const p = document.createElement("p");
    p.className = "micro";
    p.textContent = "All sections cleared! Tap Finish Run below for your full race recap.";
    ui.liveAheadList.append(p);
  } else {
    for (const item of ahead) {
      const { leg, key, etaMin } = item;
      const card = document.createElement("article");
      card.className = "ahead-leg-card liquid-glass";
      card.dataset.testid = "ahead-leg-card";
      const packSummary = summarizePackShort(leg.pack) || "Water sips only";
      const tableText = leg.station_meal ? `Table: ${leg.station_meal}` : "Pass through / refill water";
      card.innerHTML = `
        <div class="ahead-leg-top">
          <strong>${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}</strong>
          <span class="time-badge">ETA ${escapeHtml(formatDuration(etaMin))} · ${escapeHtml(compactHours(leg.predicted_minutes))}</span>
        </div>
        <p class="ahead-leg-meta">${grouped(leg.carbs_g)}g carbs · ${escapeHtml(packSummary)}</p>
        <p class="ahead-leg-table">${escapeHtml(tableText)}</p>
        <button class="text-btn ahead-done-btn" type="button" data-leg-done="${escapeHtml(key)}" data-testid="mark-section-done">✓ Done with this section</button>
      `;
      ui.liveAheadList.append(card);
    }
  }

  if (ui.liveCompletedDetails && ui.liveCompletedSummary && ui.liveCompletedList) {
    ui.liveCompletedDetails.classList.toggle("hidden", done.length === 0);
    ui.liveCompletedSummary.textContent = `Passed & cleared sections (${done.length}) — hidden until finish`;
    ui.liveCompletedList.replaceChildren();
    for (const item of done) {
      const li = document.createElement("li");
      li.className = "completed-leg-row";
      li.innerHTML = `<span>${escapeHtml(item.leg.from_name)} → ${escapeHtml(item.leg.to_name)} (${escapeHtml(formatDistance(item.leg.to_km, { digits: 1 }))})</span>`;
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

export async function markLegCompleted(key) {
  if (!key) return;
  if (!state.completedLegs.includes(key)) {
    state.completedLegs.push(key);
  }
  renderLive();
  drawMap();
  setStatus("Section cleared from view. It will appear in your Finish Recap.");
  await persist();
}

export async function reopenLegCompleted(key) {
  state.completedLegs = (state.completedLegs || []).filter((item) => item !== key);
  renderLive();
  drawMap();
  await persist();
}

export function renderRaceLog() {
  if (!ui.raceLogCard || !ui.raceLogList) return;
  const active = Boolean((state.raceMode || state.startedAt) && state.intakeHistory.length > 0);
  ui.raceLogCard.classList.toggle("hidden", !active);
  if (!active) return;
  ui.raceLogList.replaceChildren();
  const rows = Array.isArray(state.intakeHistory) ? state.intakeHistory : [];
  for (const item of rows.slice(0, 5)) {
    const li = document.createElement("li");
    li.className = "race-log-item";
    const autoTag = item.auto ? " (auto)" : "";
    const clock = item.clockText || "";
    li.textContent = `${clock} · ${item.label}${autoTag}`;
    ui.raceLogList.append(li);
  }
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
  const elapsed = elapsedMin();
  const section = sectionForDistanceKm(stop.distance_km);
  state.logged.push(stop.sequence);
  state.dueFired.add(stop.sequence);
  state.intakeHistory.unshift({
    sequence: stop.sequence,
    clockText: formatElapsedClock(elapsed),
    elapsedMin: Number(elapsed.toFixed(2)),
    label: shortIntake(stop),
    carbs_g: Math.round(stop.carbs_g || 0),
    distance_km: Number(stop.distance_km || 0),
    sectionIndex: section.index,
    sectionName: section.name,
    auto: Boolean(options.auto),
  });
  if (options.auto) {
    state.lastAutoLog = stop.sequence;
    setStatus(`Auto-logged ${stop.carbs_g.toFixed(0)} g at ${formatDistance(stop.distance_km, { digits: 1 })}.`);
  } else {
    state.lastAutoLog = null;
    setStatus(`Logged ${stop.carbs_g.toFixed(0)} g — cleared from remaining stops.`);
  }
  paintRaceControls();
  renderLive();
  drawMap();
  await persist();
}

export function undoAutoLog() {
  if (state.lastAutoLog == null) {
    return;
  }
  const sequence = state.lastAutoLog;
  const stillThere = state.livePlan?.fuel_stops.some((stop) => stop.sequence === sequence);
  state.logged = state.logged.filter((item) => item !== sequence);
  const idx = state.intakeHistory.findIndex((item) => item.sequence === sequence && item.auto);
  if (idx >= 0) state.intakeHistory.splice(idx, 1);
  state.dueFired.delete(sequence);
  state.headsUp.delete(sequence);
  state.lastAutoLog = null;
  state.pendingAuto = null;
  state.autoLogAt = 0;
  setStatus(stillThere ? "Last auto-log undone." : "That intake was already replaced by a GPS recalibration.");
  paintRaceControls();
  renderLive();
  drawMap();
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

export function openRecapModal() {
  if (!ui.recapModal || !ui.recapBody || !state.packedPlan) return;
  const plan = state.packedPlan;
  const elapsed = elapsedMin();
  const planned = Number(plan.predicted_duration_min) || 0;
  const deltaMin = Math.round(elapsed - planned);
  const deltaLabel = elapsed < 1
    ? "Not timed"
    : (deltaMin === 0 ? "Right on plan" : (deltaMin > 0 ? `+${formatDuration(deltaMin)} slower` : `−${formatDuration(Math.abs(deltaMin))} faster`));
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
    const isPassed = completed.has(key) || (passedKm > 0.1 && Number(leg.to_km) <= passedKm + 0.05);
    const statusText = isPassed ? "Passed" : (items.length ? "Logged" : "No log");
    const listHtml = items.length
      ? `<ul class="recap-items">${items.map((entry) => `<li><strong>${escapeHtml(entry.clockText || "")}</strong> · ${escapeHtml(entry.label)}</li>`).join("")}</ul>`
      : `<p class="micro">No intakes logged on this section.</p>`;
    const giHtml = legGi.length
      ? `<p class="recap-gi-flag">GI rescue triggered (${legGi.map((g) => escapeHtml(g.clockText)).join(", ")})</p>`
      : "";
    return `
      <section class="recap-leg" data-testid="recap-leg">
        <div class="recap-leg-head">
          <strong>${escapeHtml(leg.from_name)} → ${escapeHtml(leg.to_name)}</strong>
          <span class="time-badge">${escapeHtml(statusText)} · ${legCarbs}g / ${Math.round(leg.carbs_g)}g planned</span>
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
      (item) => `${item.clockText} [${item.sectionName}]: ${item.label}`,
    );
    notesInput.value = `Logged intakes:\n${lines.join("\n")}`;
  }
  raceInput?.dispatchEvent(new Event("input", { bubbles: true }));
}
