/** Fuel-stop list: a short why, and the longer reason lines behind it. */

import { detailLines, shortWhy } from "./reason_strings.js";
import { formatDuration } from "./ui_helpers.js";

function stopTitle(stop) {
  const when = formatDuration(stop.scheduled_minute);
  const name = stop.product || stop.form || "Stop";
  const grams = Math.round(Number(stop.carbs_g) || 0);
  return `${when} · ${name} · ${grams} g`;
}

/** Fills `#stop-list` from the remaining stops ahead. */
export function renderStopList(plan, options = {}) {
  const list = document.querySelector("#stop-list");
  if (!list) return;
  list.replaceChildren();
  if (!plan?.fuel_stops?.length) return;
  const logged = new Set(options.logged || []);
  const completedM = Number(options.completedDistanceM) || 0;
  const upcoming = plan.fuel_stops.filter(
    (stop) => !logged.has(stop.sequence) && (stop.distance_km * 1000 >= completedM - 50),
  );
  for (const stop of upcoming) {
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
