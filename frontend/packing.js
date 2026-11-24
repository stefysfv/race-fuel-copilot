/** Packing cards, the leg stepper, and the crew handoff card. */

import { state, ui } from "./state.js";
import { renderCrewSheet } from "./crew_sheet.js";
import { renderFuelBudget } from "./fuel_budget.js";
import { compactHours, deliveryRateLabel, escapeHtml, formatDuration, grouped } from "./ui_helpers.js";
import {
  formatDistance,
  formatDistanceRounded,
  formatElevation,
  formatPace,
  formatTemp,
  formatWeight,
} from "./units.js";

export function setupSummaryLine() {
  const plan = state.packedPlan;
  if (!plan) return;
  const dist = formatDistanceRounded(plan.distance_km, { compact: true });
  let label = dist;
  if (/salomon/i.test(plan.race_name)) label = `Salomon ${dist}`;
  else if (/vitosha/i.test(plan.race_name)) label = `Vitosha ${dist}`;
  const weight = formatWeight(plan.athlete.weight_kg);
  const carbs = Math.round(plan.athlete.carbs_g_per_hour);
  const temp = formatTemp(plan.athlete.temperature_c);
  const hours = Number(ui.hours.value) || 0;
  const minutes = Number(ui.minutes.value) || 0;
  const target = `${hours}h${String(minutes).padStart(2, "0")}m`;
  const name = document.createElement("b");
  name.textContent = label;
  const meta = document.createElement("span");
  meta.textContent = `${weight} · ${target} · ${carbs}g/h · ${temp}`;
  ui.setupSummaryText.replaceChildren(name, meta);
}

function formatClockFromStart(startMin, offsetMin) {
  if (!Number.isFinite(startMin)) return "";
  const total = ((Math.round(startMin + offsetMin) % 1440) + 1440) % 1440;
  const h = Math.floor(total / 60);
  const m = total % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

function legClockSpan(plan, leg) {
  const startMin = plan?.athlete?.start_time_minutes;
  if (!Number.isFinite(startMin)) return "";
  const legs = Array.isArray(plan?.aid_legs) ? plan.aid_legs : [];
  const idx = legs.indexOf(leg);
  if (idx < 0) return "";
  let offset = Number(plan.clock_start_min) || 0;
  for (let i = 0; i < idx; i += 1) {
    offset += Number(legs[i].predicted_minutes) || 0;
  }
  const fromClock = formatClockFromStart(startMin, offset);
  const toClock = formatClockFromStart(startMin, offset + (Number(leg.predicted_minutes) || 0));
  return `${fromClock}–${toClock}`;
}

function renderNutritionOverview(plan) {
  const root = ui.nutritionOverview;
  if (!root) return;
  if (!plan?.totals) {
    root.replaceChildren();
    root.classList.add("hidden");
    return;
  }
  root.classList.remove("hidden");
  const totals = plan.totals;
  const hours = Math.max(0.25, (Number(plan.predicted_duration_min) || 0) / 60);
  const kcal = Math.round(Number(totals.calories_kcal) || (Number(totals.carbs_g) || 0) * 4);
  const kcalHr = Math.round(kcal / hours);

  const stops = Array.isArray(plan.fuel_stops) ? plan.fuel_stops : [];
  const stationCarbs = stops
    .filter((s) => s.at_aid_station || String(s.reason || "").startsWith("Aid restock"))
    .reduce((sum, s) => sum + (Number(s.carbs_g) || 0), 0);
  const vestCarbs = Math.max(0, (Number(totals.carbs_g) || 0) - stationCarbs);
  const carbSplit = stationCarbs > 0
    ? `Vest ${grouped(vestCarbs)}g · Stations ${grouped(stationCarbs)}g`
    : `${Math.round(totals.carbs_g_per_hour_delivered || 0)} / ${Math.round(plan.athlete?.carbs_g_per_hour || 60)} g/h`;

  const foodNa = Math.round(Number(totals.sodium_mg) || 0);
  const saltCapsNa = Math.round(Number(totals.salt_caps_sodium_mg) || 0);
  const plannedNa = foodNa + saltCapsNa;
  const targetNa = Math.round((Number(plan.athlete?.sodium_mg_per_hour) || 0) * hours);
  const capMg = Math.max(50, Number(plan.athlete?.salt_cap_mg) || 200);
  const naGap = Math.max(0, targetNa - plannedNa);
  const extraCaps = naGap >= capMg ? Math.round(naGap / capMg) : 0;
  const magMg = Math.round(Number(totals.magnesium_mg) || 0);
  const magNote = magMg >= 40 ? ` · ${magMg} mg Mg` : "";
  const naSub = extraCaps > 0
    ? `Est. sweat ~${grouped(targetNa)} mg (~${extraCaps} extra caps)${magNote}`
    : `Est. sweat ~${grouped(targetNa)} mg · covered${magNote}`;

  const cafMg = Math.round(Number(totals.caffeine_mg) || 0);
  const cafCap = Math.round(Number(totals.caffeine_cap_mg) || 0);
  const startMin = plan.athlete?.start_time_minutes;
  const cafStops = stops.filter((s) => (Number(s.caffeine_mg) || 0) > 0);
  const cafTimes = cafStops.slice(0, 4).map((s) => (
    Number.isFinite(startMin)
      ? formatClockFromStart(startMin, Number(s.scheduled_minute) || 0)
      : compactHours(s.scheduled_minute)
  ));
  const cafSub = cafTimes.length
    ? `Timed at ${cafTimes.join(", ")}`
    : "No caffeinated gels packed";

  const clockLine = Number.isFinite(startMin)
    ? `${formatClockFromStart(startMin, Number(plan.clock_start_min) || 0)} → ${formatClockFromStart(startMin, (Number(plan.clock_start_min) || 0) + (Number(plan.predicted_duration_min) || 0))}`
    : compactHours(plan.predicted_duration_min);

  root.innerHTML = `
    <div class="nutrition-overview-head">
      <h3>Race Nutrition Summary</h3>
      <span class="nutrition-overview-clock">${escapeHtml(clockLine)}</span>
    </div>
    <div class="nutrition-grid">
      <div class="nutrition-stat">
        <span class="stat-kicker">Calories</span>
        <strong>${grouped(kcal)} kcal</strong>
        <span class="stat-sub">~${grouped(kcalHr)} kcal/h scheduled</span>
      </div>
      <div class="nutrition-stat">
        <span class="stat-kicker">Carbs</span>
        <strong>${grouped(totals.carbs_g)}g</strong>
        <span class="stat-sub">${escapeHtml(carbSplit)}</span>
      </div>
      <div class="nutrition-stat">
        <span class="stat-kicker">Sodium / Salt</span>
        <strong>${grouped(plannedNa)} mg</strong>
        <span class="stat-sub">${escapeHtml(naSub)}</span>
      </div>
      <div class="nutrition-stat">
        <span class="stat-kicker">Caffeine</span>
        <strong>${cafMg} / ${cafCap} mg</strong>
        <span class="stat-sub">${escapeHtml(cafSub)}</span>
      </div>
    </div>`;
}

export function renderPacking() {
  const isLiveGps = Boolean(
    state.livePlan
    && state.snap
    && (state.livePlan !== state.packedPlan || (state.passedDistanceM || 0) > 50),
  );
  const plan = isLiveGps ? state.livePlan : state.packedPlan;
  ui.packing.replaceChildren();
  ui.warnings.replaceChildren();
  renderNutritionOverview(plan);
  renderFuelBudget(plan);
  if (!plan) {
    ui.summary.classList.add("hidden");
    ui.exportCard.disabled = true;
    ui.packing.classList.remove("is-all");
    renderLegStepper([]);
    if (ui.vestUsage) {
      ui.vestUsage.textContent = "";
      ui.vestUsage.classList.add("hidden");
    }
    closeCrewModal();
    return;
  }
  const athlete = plan.athlete;
  ui.summary.classList.remove("hidden");
  ui.summary.textContent = `${state.packedPlan?.courseFileName || plan.race_name} · ${formatDistance(plan.distance_km, { digits: 1 })} · ${formatDuration(plan.predicted_duration_min)} · ${formatPace(athlete.flat_pace_min_per_km)} · ${deliveryRateLabel(plan)} · caffeine ${Math.round(plan.totals?.caffeine_mg || 0)}/${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg`;
  const vestLine = plan.vest_inventory_summary || "";
  if (ui.vestUsage) {
    ui.vestUsage.textContent = vestLine;
    ui.vestUsage.classList.toggle("hidden", !vestLine);
  }
  for (const warning of plan.warnings) {
    const text = String(warning);
    if (text.startsWith("Short Race Protocol") || /track jumps/i.test(text)) continue;
    const item = document.createElement("li");
    item.textContent = text;
    ui.warnings.append(item);
  }
  const legs = plan.aid_legs;
  if (state.legView !== "all" && (state.legView < 0 || state.legView >= legs.length)) {
    state.legView = 0;
  }
  renderLegStepper(legs);
  const shown = state.legView === "all" ? legs : (legs[state.legView] ? [legs[state.legView]] : []);
  ui.packing.classList.toggle("is-all", state.legView === "all");
  if (isLiveGps) {
    const liveNote = document.createElement("p");
    liveNote.className = "crew-live-banner";
    liveNote.textContent = `Live GPS active · ${formatDistance((state.passedDistanceM || 0) / 1000, { digits: 1 })} passed · Showing updated legs ahead.`;
    ui.packing.append(liveNote);
  }
  if (legs.length === 1 && Number(plan.distance_km) >= 20) {
    const aidNote = document.createElement("p");
    aidNote.className = "micro aid-missing-note";
    aidNote.textContent = "No aid stations in this plan — add them in Setup to include station food and refills.";
    ui.packing.append(aidNote);
  }
  for (const leg of shown) {
    ui.packing.append(packCard(leg, plan));
  }
  if (shown.length) ui.packing.append(hydrationNote());
  ui.exportCard.disabled = false;
}

export function renderLegStepper(legs) {
  ui.legSwitch?.classList.toggle("hidden", legs.length <= 1);
  const select = document.querySelector("#leg-select");
  const prev = document.querySelector("#leg-prev");
  const next = document.querySelector("#leg-next");
  if (!select || !prev || !next) return;
  select.replaceChildren();
  if (!legs.length) {
    prev.disabled = true;
    next.disabled = true;
    return;
  }
  legs.forEach((leg, index) => {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `Leg ${index + 1} of ${legs.length} · ${leg.from_name} → ${leg.to_name} (${formatDistance(leg.to_km, { digits: 1 })})`;
    select.append(option);
  });
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = "All Legs";
  select.append(all);
  select.value = state.legView === "all" ? "all" : String(state.legView);
  const index = state.legView === "all" ? -1 : state.legView;
  document.querySelector("#leg-caption").textContent =
    index < 0 ? all.textContent : `Leg ${index + 1} of ${legs.length}`;
  prev.disabled = index <= 0;
  next.disabled = index < 0 || index >= legs.length - 1;
}

export function stepLeg(delta) {
  const legs = state.packedPlan?.aid_legs || [];
  if (!legs.length || state.legView === "all") return;
  const next = state.legView + delta;
  if (next < 0 || next >= legs.length) return;
  state.legView = next;
  renderPacking();
}

function hourlyRate(value) {
  const rate = Number(value);
  return Number.isFinite(rate) ? Math.round(rate) : 0;
}

function hydrationNote() {
  const note = document.createElement("p");
  note.className = "micro hydration-note";
  note.textContent = "Fluid is a ceiling: up to the hourly rate, drink to thirst. In a long, cool race, drinking the whole planned volume can outrun thirst and dilute blood sodium. Sodium: typical range, adjust for sweat saltiness for this temperature.";
  return note;
}

function legScheduledSodium(plan, leg) {
  if (!plan?.fuel_stops?.length) return 0;
  const fromKm = Number(leg.from_km) || 0;
  const toKm = Number(leg.to_km) || 0;
  return plan.fuel_stops
    .filter((stop) => {
      const km = Number(stop.distance_km) || 0;
      return km > fromKm + 0.01 && km <= toKm + 0.05;
    })
    .reduce((sum, stop) => sum + (Number(stop.sodium_mg) || 0), 0);
}

function legScheduledCarbs(plan, leg) {
  if (!plan?.vest_inventory_summary) return null;
  const legs = Array.isArray(plan.aid_legs) ? plan.aid_legs : [];
  const legIndex = legs.indexOf(leg);
  if (legIndex < 0) return null;
  const stops = Array.isArray(plan.fuel_stops) ? plan.fuel_stops : [];
  let trailCarbs = 0;
  for (const stop of stops) {
    if (stop.at_aid_station) continue;
    const km = Number(stop.distance_km) || 0;
    let idx = Math.max(0, legs.length - 1);
    for (let i = 0; i < legs.length; i += 1) {
      if (km < legs[i].to_km - 1e-6 || i === legs.length - 1) {
        if (km >= legs[i].from_km - 1e-6) {
          idx = i;
          break;
        }
      }
    }
    if (idx === legIndex) trailCarbs += Number(stop.carbs_g) || 0;
  }
  return trailCarbs + (Number(leg.station_carbs_g) || 0);
}

export function packCard(leg, plan = state.packedPlan) {
  const card = document.createElement("article");
  card.className = "pack-card leg-card";
  const fluidRate = hourlyRate(plan?.athlete?.fluid_ml_per_hour);
  const sodiumRate = hourlyRate(plan?.athlete?.sodium_mg_per_hour);
  const scheduledCarbs = legScheduledCarbs(plan, leg);
  const shownCarbs = scheduledCarbs === null ? leg.carbs_g : scheduledCarbs;
  const carbsLabel = scheduledCarbs !== null && scheduledCarbs + 5 < leg.carbs_g
    ? `Carbs packed (target ${grouped(leg.carbs_g)}g)`
    : "Carbs";
  const salt = leg.pack.filter((item) => /salt capsule/i.test(item.item));
  const chew = leg.pack.filter((item) => !/flask/i.test(item.item) && !/salt capsule/i.test(item.item));
  const sips = leg.pack.filter((item) => /flask/i.test(item.item));
  const lines = [...chew, ...sips, ...salt].map((item) => carryLine(item)).join("");
  const legHours = Math.max(0, (Number(leg.predicted_minutes) || 0) / 60);
  const legSweatNa = Math.round(sodiumRate * legHours);
  const legFoodNa = Math.round(legScheduledSodium(plan, leg));
  const hasSaltCaps = Boolean(plan?.athlete?.has_salt_caps);
  const saltGapNote = !hasSaltCaps && salt.length === 0 && legSweatNa >= 250 && legFoodNa < legSweatNa * 0.7
    ? `<p class="micro salt-gap-note">Salt: ~${grouped(legFoodNa)} mg food vs ~${grouped(legSweatNa)} mg sweat — add Salt Caps/Mix or grab extra salt at stations.</p>`
    : "";
  const trail = (chew.length
    ? `<ul class="carry-list">${lines}</ul>`
    : `<p class="trail-clear">✓ No trail eating needed — focus on pace &amp; hydration sips</p>${lines ? `<ul class="carry-list">${lines}</ul>` : ""}`) + saltGapNote;
  const station = escapeHtml(leg.to_name);
  const marker = formatDistance(leg.to_km, { digits: 1 }).toUpperCase();
  const meal = leg.station_meal
    ? `<p class="eat-note">${escapeHtml(eatLabel(leg))}</p>`
    : `<p class="pass-badge">Water refill only if needed</p>`;
  const coach = leg.note ? `<p class="coach-note">${escapeHtml(leg.note)}</p>` : "";
  card.innerHTML = `
    <div class="leg-card-header">
      <div class="pack-top">
        <h2>${escapeHtml(leg.from_name)} → ${station} <span class="km">(${escapeHtml(formatDistance(leg.distance_km, { digits: 1 }))})</span></h2>
        <span class="time-badge">${legClockSpan(plan, leg) ? `${escapeHtml(legClockSpan(plan, leg))} · ` : ""}${compactHours(leg.predicted_minutes)} · ${escapeHtml(formatElevation(legGain(leg), { signed: true, compact: true }))}</span>
      </div>
      ${coach}
      <div class="macros">
        <div><strong>${grouped(shownCarbs)}g</strong><span>${carbsLabel}</span></div>
        <div><strong class="macro-rate">up to ${grouped(fluidRate)} ml/h</strong><span class="macro-plain">drink to thirst</span></div>
        <div><strong class="macro-rate">~${grouped(sodiumRate)} mg/h</strong><span class="macro-plain">est. sweat sodium</span></div>
      </div>
    </div>
    <div class="leg-card-scroll">
      <section class="carry-block">
        <p class="kicker">On the trail</p>
        ${trail}
      </section>
      <section class="table-block">
        <p class="kicker">At ${station} · ${escapeHtml(marker)}</p>
        ${meal}
      </section>
    </div>`;
  return card;
}

export function legGain(leg) {
  const segments = state.packedPlan?.segments || [];
  const from = leg.from_km * 1000;
  const to = leg.to_km * 1000;
  let gain = 0;
  for (const segment of segments) {
    if (segment.end_distance_m <= from || segment.start_distance_m >= to) continue;
    gain += segment.elevation_gain_m || 0;
  }
  return Math.round(gain);
}

export function carryLine(item) {
  const name = escapeHtml(
    String(item.item)
      .replace("Soft flask 500 ml", "500ml Flask")
      .replace("Salt capsule", "Salt cap"),
  );
  const cleanDetail = item.detail
    ? String(item.detail).replace(/\s*[·•-]?\s*from the packed[^·]*$/i, "").trim()
    : "";
  const detail = cleanDetail ? `<span class="carry-detail">${escapeHtml(cleanDetail)}</span>` : "";
  return `<li><span class="qty">${item.quantity}×</span> ${name}${detail}</li>`;
}

export function eatLabel(leg) {
  if (!leg.station_meal) return "Nothing on the table for this stop.";
  if (leg.station_meal.startsWith("Aid Station Plate")) {
    return `Potatoes / banana + cola (${Math.round(leg.station_carbs_g)}g)`;
  }
  if (/\(\d+\s*g\)/i.test(leg.station_meal)) return leg.station_meal;
  return `${leg.station_meal} (${Math.round(leg.station_carbs_g)}g)`;
}

export function exportRaceCard() {
  renderCrewSheet();
}

export function closeCrewModal() {
  ui.crewModal.classList.add("hidden");
}
