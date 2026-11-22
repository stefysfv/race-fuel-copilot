/** Print-friendly, shareable, and GPS-adaptive crew sheet (client-side only). */

import { state, ui } from "./state.js";
import { deliveryRateLabel, elapsedMin, escapeHtml, formatDuration, formatTimeOfDay, grouped } from "./ui_helpers.js";
import { formatDistance, formatPace, formatTemp } from "./units.js";

export const GI_RESCUE_STEPS = [
  "Stop solid food and thick gels right away. Do not force calories on a knotted or sloshing stomach.",
  "Switch to small sips of plain water, weak electrolyte drink, flat cola, or warm broth every 5-10 minutes.",
  "Slow the effort for 15-20 minutes until nausea eases. In warm weather, cool the neck, wrists, and head.",
  "Once the stomach settles, restart gently with dilute liquid carbs or a small salty bite before resuming normal gels.",
];

export function legKey(leg) {
  return `${leg.from_name}->${leg.to_name}@${Number(leg.to_km || 0).toFixed(2)}`;
}

function eatSummary(leg) {
  if (!leg?.station_meal) return "";
  if (leg.station_meal.startsWith("Aid Station Plate")) {
    return `Potatoes / banana + cola (${Math.round(leg.station_carbs_g)}g)`;
  }
  if (/\(\d+\s*g\)/i.test(leg.station_meal)) return leg.station_meal;
  return `${leg.station_meal} (${Math.round(leg.station_carbs_g)}g)`;
}

function summarizePack(pack = []) {
  const food = [];
  const fluid = [];
  const salt = [];
  for (const item of pack) {
    const label = `${item.quantity}x ${String(item.item)
      .replace("Soft flask 500 ml", "500ml flask")
      .replace("Salt capsule", "Salt cap")}`;
    if (/flask/i.test(item.item)) fluid.push(label);
    else if (/salt capsule/i.test(item.item)) salt.push(label);
    else food.push(label);
  }
  return { food, fluid, salt };
}

function formatHandOver(incomingLeg, nextLeg, plan) {
  const eat = eatSummary(incomingLeg);
  if (!nextLeg) {
    return eat ? `Eat: ${eat} · Finish line recovery` : "Finish line — dry layer & recovery drink";
  }
  const parts = [];
  if (eat) parts.push(`Eat here: ${eat}`);
  const next = summarizePack(nextLeg.pack);
  if (next.food.length) parts.push(`Food: ${next.food.join(", ")}`);
  const fluidRate = Math.round(Number(plan.athlete?.fluid_ml_per_hour) || 0);
  if (next.fluid.length) {
    parts.push(`Fluid: ${next.fluid.join(", ")} (up to ${grouped(fluidRate)} ml/h)`);
  } else if (fluidRate > 0) {
    parts.push(`Fluid: refill flasks (up to ${grouped(fluidRate)} ml/h)`);
  }
  const sodiumRate = Math.round(Number(plan.athlete?.sodium_mg_per_hour) || 0);
  if (next.salt.length) {
    parts.push(`Salt: ${next.salt.join(", ")}`);
  } else if (sodiumRate > 0) {
    parts.push(`Salt: ~${grouped(sodiumRate)} mg/h target`);
  }
  return parts.join(" | ") || "Water refill only";
}

function formatCarrying(incomingLeg) {
  const carried = summarizePack(incomingLeg.pack);
  const items = [...carried.fluid, ...carried.food, ...carried.salt];
  if (!items.length) return "Empty vest / water only";
  return `From ${incomingLeg.from_name}: ${items.join(", ")}`;
}

export function buildCrewRows(plan, aidStations = [], options = {}) {
  if (!plan?.aid_legs?.length) return [];
  const startClock = Number(plan.clock_start_min) || 0;
  const paceScale = Number(options.paceScale) > 0 ? Number(options.paceScale) : 1;
  const completedKeys = new Set(options.completedLegs || []);
  let cursor = startClock;
  const rows = [];
  plan.aid_legs.forEach((leg, index) => {
    cursor += (Number(leg.predicted_minutes) || 0) * paceScale;
    const remainingFromNow = Math.max(0, cursor - startClock);
    const station = aidStations.find((item) => item.name === leg.to_name) || null;
    const cutoff = station?.cutoff_hours ? formatDuration(Number(station.cutoff_hours) * 60) : "—";
    const nextLeg = plan.aid_legs[index + 1] || null;
    if (completedKeys.has(legKey(leg)) && nextLeg) {
      return;
    }
    rows.push({
      name: leg.to_name,
      distance_km: Number(leg.to_km) || 0,
      drop_bag: Boolean(station?.drop_bag),
      eta_fast_min: startClock + remainingFromNow * 0.9,
      eta_plan_min: cursor,
      eta_slow_min: startClock + remainingFromNow * 1.1,
      cutoff,
      hand_over: formatHandOver(leg, nextLeg, plan),
      carrying: formatCarrying(leg),
    });
  });
  return rows;
}

export function crewSheetText(plan, aidStations = [], options = {}) {
  const rows = buildCrewRows(plan, aidStations, options);
  const athlete = plan.athlete || {};
  const liveTag = options.isLiveGps
    ? ` [LIVE GPS UPDATED · ${formatDistance((options.passedDistanceM || 0) / 1000, { digits: 1 })} passed]`
    : "";
  const lines = [
    `CREW SHEET: ${plan.race_name}${liveTag}`,
    `Distance: ${formatDistance(plan.distance_km, { digits: 1 })} | Plan time: ${formatDuration(plan.predicted_duration_min)} | Pace: ${formatPace(athlete.flat_pace_min_per_km || 0)}`,
    `Targets: ${deliveryRateLabel(plan)} | Fluid up to ${grouped(athlete.fluid_ml_per_hour || 0)} ml/h | Sodium ~${grouped(athlete.sodium_mg_per_hour || 0)} mg/h | Temp ${formatTemp(athlete.temperature_c || 0)}`,
    `Caffeine: ${Math.round(plan.totals?.caffeine_mg || 0)} / ${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg`,
    "",
    "AID STATIONS",
  ];
  for (const row of rows) {
    const bag = row.drop_bag ? " [DROP BAG]" : "";
    lines.push(
      `- ${row.name} (${formatDistance(row.distance_km, { digits: 1 })})${bag}`,
      `  ETA: Fast (-10%) ${formatDuration(row.eta_fast_min)} | Plan ${formatDuration(row.eta_plan_min)} | Slow (+10%) ${formatDuration(row.eta_slow_min)} | Cutoff: ${row.cutoff}`,
      `  Hand over: ${row.hand_over}`,
      `  Runner carries in: ${row.carrying}`,
    );
  }
  lines.push("", "IF STOMACH GOES BAD (GI RESCUE)");
  GI_RESCUE_STEPS.forEach((step, idx) => lines.push(`${idx + 1}. ${step}`));
  return lines.join("\n");
}

async function shareCrewSheet(plan, aidStations, options) {
  const text = crewSheetText(plan, aidStations, options);
  const title = `Crew Sheet - ${plan.race_name}`;
  const status = ui.raceCard?.querySelector("#crew-share-status");
  const plainWrap = ui.raceCard?.querySelector("#crew-plain-wrap");
  const plainBox = ui.raceCard?.querySelector("#crew-plain-text");

  if (typeof navigator !== "undefined" && typeof navigator.share === "function") {
    try {
      await navigator.share({ title, text });
      if (status) status.textContent = "Shared.";
      return;
    } catch (error) {
      if (error?.name === "AbortError") return;
    }
  }
  if (plainBox) plainBox.value = text;
  plainWrap?.classList.remove("hidden");
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      if (status) status.textContent = "Copied plain text to clipboard (also shown below).";
      return;
    }
  } catch {
    /* fallback below */
  }
  if (status) status.textContent = "Plain text ready below — select and copy.";
}

export function renderCrewSheet() {
  const isLiveGps = Boolean(
    state.livePlan
    && state.snap
    && (state.livePlan !== state.packedPlan || (state.passedDistanceM || 0) > 50),
  );
  const plan = isLiveGps ? state.livePlan : state.packedPlan;
  if (!plan || !ui.crewModal || !ui.raceCard) return;
  const latestLog = (state.sectionLogs || []).slice(-1)[0] || null;
  const useLogScale = Boolean(
    latestLog
    && (!state.lastGpsRecalAt || latestLog.clockMs > state.lastGpsRecalAt)
    && latestLog.plannedCumMin > 2
    && latestLog.actualElapsedMin > 1,
  );
  const paceScale = useLogScale
    ? Math.min(1.85, Math.max(0.7, latestLog.actualElapsedMin / latestLog.plannedCumMin))
    : 1;
  const options = {
    isLiveGps,
    paceScale,
    passedDistanceM: state.passedDistanceM || 0,
    completedLegs: (isLiveGps || state.startedAt) ? (state.completedLegs || []) : [],
  };
  const nowMs = state.pausedAt || Date.now();
  const elapsedNow = state.startedAt ? elapsedMin() : null;
  const rows = buildCrewRows(plan, state.aidStations, options);
  const athlete = plan.athlete || {};
  const preference = String(state.fuelPreference || "gels_first").replaceAll("_", " ");
  const totalFinishMin = (Number(plan.clock_start_min) || 0) + Number(plan.predicted_duration_min || 0);
  const liveBanner = isLiveGps
    ? `<p class="crew-live-banner" data-testid="crew-live-banner">Live GPS updated · ${escapeHtml(formatDistance((state.passedDistanceM || 0) / 1000, { digits: 1 }))} passed · Showing remaining stations &amp; updated ETAs</p>`
    : "";
  const bodyRows = rows.map((row) => {
    const bag = row.drop_bag ? ' <span class="crew-bag">Drop bag</span>' : "";
    return `<tr data-testid="crew-row">
      <td class="crew-cell-station" data-label="Station"><span><strong>${escapeHtml(row.name)}</strong>${bag}</span><span class="crew-dist">${escapeHtml(formatDistance(row.distance_km, { digits: 1 }))}</span></td>
      <td class="crew-cell-eta" data-label="Fast (−10%)" data-testid="crew-eta-fast">${escapeHtml(formatDuration(row.eta_fast_min))}</td>
      <td class="crew-cell-eta crew-cell-eta-plan" data-label="Plan ETA" data-testid="crew-eta-plan"><strong>${escapeHtml(formatDuration(row.eta_plan_min))}</strong>${elapsedNow !== null ? `<span class="crew-clock">~${escapeHtml(formatTimeOfDay(nowMs + Math.max(0, row.eta_plan_min - elapsedNow) * 60000, 5))}</span>` : ""}</td>
      <td class="crew-cell-eta" data-label="Slow (+10%)" data-testid="crew-eta-slow">${escapeHtml(formatDuration(row.eta_slow_min))}</td>
      <td class="crew-cell-cutoff" data-label="Cutoff">${escapeHtml(row.cutoff)}</td>
      <td class="crew-cell-handover" data-label="Hand over">${escapeHtml(row.hand_over)}</td>
      <td class="crew-cell-carry" data-label="Runner carries in">${escapeHtml(row.carrying)}</td>
    </tr>`;
  }).join("");
  const rescueItems = GI_RESCUE_STEPS.map((step) => `<li>${escapeHtml(step)}</li>`).join("");

  ui.crewModal.classList.remove("hidden");
  ui.raceCard.innerHTML = `
    <div class="crew-header" data-testid="crew-sheet">
      <h2>${escapeHtml(plan.race_name)} — Crew Sheet</h2>
      ${liveBanner}
      <p class="crew-meta">${escapeHtml(formatDistance(state.packedPlan?.distance_km || plan.distance_km, { digits: 1 }))} · ${isLiveGps ? "Updated finish" : "Plan"} ${escapeHtml(formatDuration(isLiveGps ? totalFinishMin : plan.predicted_duration_min))} · ${escapeHtml(formatPace(athlete.flat_pace_min_per_km || 0))} · ${escapeHtml(preference)}</p>
      <p class="crew-meta">Carbs ${escapeHtml(deliveryRateLabel(plan))} · Fluid up to ${escapeHtml(grouped(athlete.fluid_ml_per_hour || 0))} ml/h · Sodium ~${escapeHtml(grouped(athlete.sodium_mg_per_hour || 0))} mg/h · Caffeine ${Math.round(plan.totals?.caffeine_mg || 0)}/${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg</p>
    </div>
    <div class="crew-table-wrap">
      <table class="crew-table" data-testid="crew-table">
        <thead>
          <tr>
            <th>Station</th>
            <th>Fast (−10%)</th>
            <th>Plan ETA</th>
            <th>Slow (+10%)</th>
            <th>Cutoff</th>
            <th>Hand over</th>
            <th>Runner carries in</th>
          </tr>
        </thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>
    <section class="crew-rescue" data-testid="crew-gi-rescue">
      <h3>If the runner's stomach goes bad (GI rescue)</h3>
      <ol>${rescueItems}</ol>
    </section>
    <div class="crew-actions">
      <button id="print-card" class="thumb-btn primary" type="button" data-testid="crew-print">Print</button>
      <button id="share-card" class="thumb-btn" type="button" data-testid="crew-share">Share</button>
    </div>
    <p id="crew-share-status" class="micro" role="status" data-testid="crew-share-status"></p>
    <div id="crew-plain-wrap" class="crew-plain-wrap hidden">
      <label class="kicker" for="crew-plain-text">Plain text copy</label>
      <textarea id="crew-plain-text" data-testid="crew-plain-text" rows="8" readonly></textarea>
    </div>
  `;
  ui.raceCard.querySelector("#print-card")?.addEventListener("click", () => window.print());
  ui.raceCard.querySelector("#share-card")?.addEventListener("click", () => {
    shareCrewSheet(plan, state.aidStations, options);
  });
}
