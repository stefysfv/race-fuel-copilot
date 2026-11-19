/** Fuel budget card. Words for the additive plan.fuel_budget field. */

import { ui } from "./state.js";

/** Where a forfeited code's grams went. A missing code shows the code itself. */
const FORFEIT_LABEL = {
  SKIPPED_RED_ZONE: "Red zones",
  GI_RESCUE_RED: "Rescue on red",
  QUIET_WINDOW: "Quiet window",
  DESCENT_LOCK: "Fast descents",
  YELLOW_HALF: "Yellow half left behind",
  GI_RESCUE_HALF: "Rescue half cut",
  NO_VEST_FOOD: "Empty vest",
  NO_CLIMB_FOOD: "No climb food",
  UNPLACED: "No ground in the window",
};

const TIPS = [
  "Eat more at aid stations.",
  "Pre-race loading covers the opening.",
  "Adjust your stomach settings.",
];

function grams(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "0";
  return String(Math.round(number));
}

function sentence(budget) {
  const red = Math.round(Number(budget.red_share_of_course_pct) || 0);
  const delivered = grams(budget.delivered_g_per_h);
  const target = grams(budget.target_g_per_h);
  const rate = `you'll get about ${delivered} g/h of your ${target} g/h target.`;
  if (red > 0) {
    return `${red}% of this course is in no-fuel zones, so ${rate}`;
  }
  return `You'll get about ${delivered} g/h of your ${target} g/h target.`;
}

function missingRows(budget) {
  return Object.entries(budget.forfeited_by_code || {})
    .map(([code, amount]) => ({ code, grams: Number(amount) }))
    .filter((row) => Number.isFinite(row.grams) && row.grams > 0)
    .sort((left, right) => right.grams - left.grams || left.code.localeCompare(right.code));
}

export function renderFuelBudget(plan) {
  const node = ui.fuelBudget;
  if (!node) return;
  const budget = plan?.fuel_budget;
  if (!plan || !budget) {
    node.replaceChildren();
    node.classList.add("hidden");
    return;
  }
  node.classList.remove("hidden", "tier-ok", "tier-info", "tier-warn");
  node.classList.add(`tier-${budget.tier || "ok"}`);
  node.replaceChildren();

  const title = document.createElement("h2");
  title.textContent = "Fuel budget";
  node.append(title);

  const line = document.createElement("p");
  line.className = "budget-sentence";
  line.textContent = sentence(budget);
  node.append(line);

  if (budget.note) {
    const note = document.createElement("p");
    note.className = "budget-note";
    note.textContent = budget.note;
    node.append(note);
  }

  const ratio = Number(budget.delivery_ratio);
  const pct = Number.isFinite(ratio) ? Math.max(0, Math.min(100, ratio * 100)) : 0;
  const meter = document.createElement("div");
  meter.className = "budget-meter";
  meter.setAttribute("role", "meter");
  meter.setAttribute("aria-label", "Delivered carbs against the target");
  meter.setAttribute("aria-valuemin", "0");
  meter.setAttribute("aria-valuemax", "100");
  meter.setAttribute("aria-valuenow", String(Math.round(pct)));
  const fill = document.createElement("span");
  fill.className = "budget-fill";
  fill.style.width = `${pct}%`;
  meter.append(fill);
  node.append(meter);

  const rate = document.createElement("p");
  rate.className = "budget-rate";
  rate.textContent = `${grams(budget.delivered_g_per_h)} / ${grams(budget.target_g_per_h)} g/h`;
  node.append(rate);

  const missing = document.createElement("ul");
  missing.className = "budget-missing";
  const rows = missingRows(budget);
  if (!rows.length) {
    const item = document.createElement("li");
    item.textContent = "No carbs were left behind.";
    missing.append(item);
  } else {
    for (const row of rows) {
      const item = document.createElement("li");
      item.textContent = `${grams(row.grams)} g · ${FORFEIT_LABEL[row.code] || row.code}`;
      missing.append(item);
    }
  }
  node.append(missing);

  const tips = document.createElement("ul");
  tips.className = "budget-tips";
  for (const tip of TIPS) {
    const item = document.createElement("li");
    item.textContent = tip;
    tips.append(item);
  }
  node.append(tips);
}
