/** Fuel-stop list: a short why, and the longer reason lines behind it. */

import { detailLines, shortWhy } from "./reason_strings.js";
import { formatDuration } from "./ui_helpers.js";

function stopTitle(stop) {
  const when = formatDuration(stop.scheduled_minute);
  const name = stop.product || stop.form || "Stop";
  const grams = Math.round(Number(stop.carbs_g) || 0);
  return `${when} · ${name} · ${grams} g`;
}

/** Fills `#stop-list` from the plan currently on screen. */
export function renderStopList(plan) {
  const list = document.querySelector("#stop-list");
  if (!list) return;
  list.replaceChildren();
  if (!plan?.fuel_stops?.length) return;
  for (const stop of plan.fuel_stops) {
    const item = document.createElement("li");
    item.className = "stop-row";
    const title = document.createElement("p");
    title.className = "stop-main";
    title.textContent = stopTitle(stop);
    const details = document.createElement("details");
    details.className = "stop-why";
    const summary = document.createElement("summary");
    summary.textContent = shortWhy(stop);
    details.append(summary);
    for (const line of detailLines(stop)) {
      const paragraph = document.createElement("p");
      paragraph.textContent = line;
      details.append(paragraph);
    }
    item.append(title, details);
    list.append(item);
  }
}
