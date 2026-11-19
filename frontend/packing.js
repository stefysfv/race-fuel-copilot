/** Packing cards, the leg stepper, and the crew handoff card. */

import { state, ui } from "./state.js";
import { renderFuelBudget } from "./fuel_budget.js";
import { compactHours, deliveryRateLabel, escapeHtml, formatDuration, grouped } from "./ui_helpers.js";

export function setupSummaryLine() {
  const plan = state.packedPlan;
  const km = Math.round(plan.distance_km);
  let label = `${km}km`;
  if (/salomon/i.test(plan.race_name)) label = `Salomon ${km}km`;
  else if (/vitosha/i.test(plan.race_name)) label = `Vitosha ${km}km`;
  const weight = Math.round(Number(plan.athlete.weight_kg));
  const carbs = Math.round(plan.athlete.carbs_g_per_hour);
  const temp = Math.round(Number(plan.athlete.temperature_c));
  const hours = Number(ui.hours.value) || 0;
  const minutes = Number(ui.minutes.value) || 0;
  const target = `${hours}h${String(minutes).padStart(2, "0")}m`;
  const name = document.createElement("b");
  name.textContent = label;
  const meta = document.createElement("span");
  meta.textContent = `${weight}kg · ${target} · ${carbs}g/h · ${temp}°C`;
  ui.setupSummaryText.replaceChildren(name, meta);
}

export function renderPacking() {
  const plan = state.packedPlan;
  ui.packing.replaceChildren();
  ui.warnings.replaceChildren();
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
  ui.summary.textContent = `${plan.race_name} · ${formatDuration(plan.predicted_duration_min)} · ${athlete.flat_pace_min_per_km.toFixed(2)} min/km · ${deliveryRateLabel(plan)} · caffeine ${Math.round(plan.totals?.caffeine_mg || 0)}/${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg`;
  const vestLine = plan.vest_inventory_summary || "";
  if (ui.vestUsage) {
    ui.vestUsage.textContent = vestLine;
    ui.vestUsage.classList.toggle("hidden", !vestLine);
  }
  for (const warning of plan.warnings) {
    if (String(warning).startsWith("Short Race Protocol")) continue;
    const item = document.createElement("li");
    item.textContent = warning;
    ui.warnings.append(item);
  }
  const legs = plan.aid_legs;
  if (state.legView !== "all" && (state.legView < 0 || state.legView >= legs.length)) {
    state.legView = 0;
  }
  renderLegStepper(legs);
  const shown = state.legView === "all" ? legs : (legs[state.legView] ? [legs[state.legView]] : []);
  ui.packing.classList.toggle("is-all", state.legView === "all");
  if (shown.length) ui.packing.append(hydrationNote());
  for (const leg of shown) {
    ui.packing.append(packCard(leg));
  }
  ui.exportCard.disabled = false;
}

export function renderLegStepper(legs) {
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
    const km = Number(leg.to_km).toFixed(1);
    option.textContent = `Leg ${index + 1} of ${legs.length} · ${leg.from_name} → ${leg.to_name} (${km} km)`;
    select.append(option);
  });
  const all = document.createElement("option");
  all.value = "all";
  all.textContent = "All Legs";
  select.append(all);
  select.value = state.legView === "all" ? "all" : String(state.legView);
  const index = state.legView === "all" ? -1 : state.legView;
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
  note.textContent = "Fluid is a ceiling: up to the hourly rate, drink to thirst. In a long, cool race, drinking the whole planned volume can outrun thirst and dilute blood sodium. Sodium is a typical range for this temperature; adjust it for how salty you sweat.";
  return note;
}

export function packCard(leg) {
  const card = document.createElement("article");
  card.className = "pack-card leg-card";
  const fluidRate = hourlyRate(state.packedPlan?.athlete?.fluid_ml_per_hour);
  const sodiumRate = hourlyRate(state.packedPlan?.athlete?.sodium_mg_per_hour);
  const salt = leg.pack.filter((item) => /salt capsule/i.test(item.item));
  const chew = leg.pack.filter((item) => !/flask/i.test(item.item) && !/salt capsule/i.test(item.item));
  const sips = leg.pack.filter((item) => /flask/i.test(item.item));
  const lines = [...chew, ...sips, ...salt].map((item) => carryLine(item)).join("");
  const trail = chew.length
    ? `<ul class="carry-list">${lines}</ul>`
    : `<p class="trail-clear">✓ No trail eating needed — focus on pace &amp; hydration sips</p>${lines ? `<ul class="carry-list">${lines}</ul>` : ""}`;
  const station = escapeHtml(leg.to_name);
  const km = Number(leg.to_km).toFixed(1);
  const meal = leg.station_meal
    ? `<p class="table-where">AT ${escapeHtml(leg.to_name.toUpperCase())} CHECKPOINT TABLE (KM ${km})</p><p class="eat-note">${escapeHtml(eatLabel(leg))}</p>`
    : `<p class="pass-badge">Pass through station — water refill only if needed</p>`;
  const coach = leg.note ? `<p class="coach-note">${escapeHtml(leg.note)}</p>` : "";
  card.innerHTML = `
    <div class="leg-card-header">
      <div class="pack-top">
        <h2>${escapeHtml(leg.from_name)} → ${station} <span class="km">(${Number(leg.distance_km).toFixed(1)} km)</span></h2>
        <span class="time-badge">${compactHours(leg.predicted_minutes)} · +${legGain(leg)}m</span>
      </div>
      ${coach}
      <div class="macros">
        <div><strong>${grouped(leg.carbs_g)}g</strong><span>Carbs</span></div>
        <div><strong class="macro-rate">up to ${grouped(fluidRate)} ml/h</strong><span class="macro-plain">drink to thirst</span></div>
        <div><strong class="macro-rate">about ${grouped(sodiumRate)} mg/h</strong><span class="macro-plain">typical range, adjust for sweat saltiness</span></div>
      </div>
    </div>
    <div class="leg-card-scroll">
      <section class="carry-block">
        <p class="kicker">On the trail (consume while moving)</p>
        ${trail}
      </section>
      <section class="table-block">
        <p class="kicker">At ${station} checkpoint table (eat before leaving)</p>
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
  const detail = item.detail ? `<span class="carry-detail">${escapeHtml(item.detail)}</span>` : "";
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
  const plan = state.packedPlan;
  if (!plan) {
    return;
  }
  let cursor = plan.clock_start_min;
  const rows = plan.aid_legs.map((leg) => {
    cursor += leg.predicted_minutes;
    const station = state.aidStations.find((item) => item.name === leg.to_name);
    const cutoff = station?.cutoff_hours ? `${station.cutoff_hours} h` : "—";
    const vest = leg.pack.map((item) => `${item.quantity}× ${item.item}`).join(", ");
    const eat = leg.station_meal ? eatLabel(leg) : "—";
    return `<tr>
      <td>${leg.to_name}<br>${leg.to_km.toFixed(1)} km</td>
      <td>${formatDuration(cursor)}</td>
      <td>${cutoff}</td>
      <td>${vest}</td>
      <td>${eat}</td>
    </tr>`;
  }).join("");
  const preference = state.fuelPreference.replaceAll("_", " ");
  ui.crewModal.classList.remove("hidden");
  ui.raceCard.innerHTML = `
    <h2>${plan.race_name}</h2>
    <p class="mt-1">Predicted ${formatDuration(plan.predicted_duration_min)} · ${preference} · caffeine ${Math.round(plan.totals?.caffeine_mg || 0)} / ${Math.round(plan.totals?.caffeine_cap_mg || 0)} mg</p>
    <table class="mt-3">
      <thead><tr><th>Station</th><th>ETA</th><th>Cutoff</th><th>Vest carry</th><th>Eat at station</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <button id="print-card" class="thumb-btn mt-3" type="button">Print</button>`;
  ui.raceCard.querySelector("#print-card")?.addEventListener("click", () => window.print());
}

export function closeCrewModal() {
  ui.crewModal.classList.add("hidden");
}
