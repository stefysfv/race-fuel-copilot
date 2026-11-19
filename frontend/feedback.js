/**
 * Post-race notes stored on this phone.
 *
 * Fill in one of these before a public build. Nothing is sent until the
 * athlete presses Send feedback.
 */

import { FEEDBACK_KEY, readJson, writeJson } from "./local_store.js";
import { state } from "./state.js";

export const FEEDBACK_EMAIL = "";
export const FEEDBACK_FORM_URL = "";

const PREVIEW = "Send feedback includes only these fields: race name, finish versus predicted time, GI trouble (none, mild, or severe), what you changed, and your note. It does not include GPS tracks, analytics, or an account. Nothing is sent until you press Send feedback.";

let editingId = "";

function activeRaceName() {
  if (state.packedPlan?.race_name) return state.packedPlan.race_name;
  if (state.livePlan?.race_name) return state.livePlan.race_name;
  const race = state.races.find((item) => item.id === state.raceId);
  return race?.name || "";
}

function entries() {
  const saved = readJson(FEEDBACK_KEY);
  return Array.isArray(saved) ? saved : [];
}

function readForm() {
  return {
    raceName: document.querySelector("#feedback-race")?.value.trim() || "",
    finishNote: document.querySelector("#feedback-finish")?.value.trim() || "",
    gi: document.querySelector("input[name='feedback-gi']:checked")?.value || "none",
    changed: document.querySelector("#feedback-changed")?.value.trim() || "",
    notes: document.querySelector("#feedback-notes")?.value.trim() || "",
  };
}

function formEntry() {
  return {
    ...readForm(),
    id: editingId || crypto.randomUUID(),
    savedAt: Date.now(),
  };
}

export function feedbackHref(entry, options = {}) {
  const email = options.email ?? FEEDBACK_EMAIL;
  const formUrl = options.formUrl ?? FEEDBACK_FORM_URL;
  const payload = {
    race: entry.raceName || "",
    finish: entry.finishNote || "",
    gi: entry.gi || "none",
    changed: entry.changed || "",
    notes: entry.notes || "",
  };
  if (formUrl) {
    const url = new URL(formUrl, options.location || window.location.href);
    for (const [key, value] of Object.entries(payload)) {
      url.searchParams.set(key, value);
    }
    return url.toString();
  }
  const lines = [
    `Race name: ${payload.race}`,
    `Finish versus predicted time: ${payload.finish}`,
    `GI trouble: ${payload.gi}`,
    `What you changed: ${payload.changed}`,
    `Notes: ${payload.notes}`,
  ];
  const subject = "Race Fuel Co-Pilot feedback";
  return `mailto:${email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(lines.join("\n"))}`;
}

function fillForm(entry) {
  const race = document.querySelector("#feedback-race");
  const finish = document.querySelector("#feedback-finish");
  const changed = document.querySelector("#feedback-changed");
  const notes = document.querySelector("#feedback-notes");
  if (race) race.value = entry?.raceName || "";
  if (finish) finish.value = entry?.finishNote || "";
  if (changed) changed.value = entry?.changed || "";
  if (notes) notes.value = entry?.notes || "";
  const gi = entry?.gi || "none";
  for (const input of document.querySelectorAll("input[name='feedback-gi']")) {
    input.checked = input.value === gi;
  }
  editingId = entry?.id || "";
  paintSendLink();
}

function paintSendLink() {
  const link = document.querySelector("#send-feedback");
  if (!link) return;
  link.href = feedbackHref(readForm());
}

function paintList() {
  const list = document.querySelector("#feedback-saved");
  if (!list) return;
  list.replaceChildren();
  const saved = entries();
  if (!saved.length) {
    const empty = document.createElement("li");
    empty.textContent = "No notes saved on this phone yet.";
    list.append(empty);
    return;
  }
  for (const entry of saved) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "feedback-entry";
    button.dataset.testid = "feedback-entry";
    button.dataset.id = entry.id;
    const when = new Date(entry.savedAt);
    const stamp = Number.isNaN(when.getTime()) ? "" : when.toLocaleString();
    button.textContent = `${entry.raceName || "Untitled"} · ${entry.gi || "none"}${stamp ? ` · ${stamp}` : ""}`;
    button.addEventListener("click", () => fillForm(entry));
    item.append(button);
    list.append(item);
  }
}

export function paintFeedback() {
  const note = document.querySelector("#feedback-preview");
  if (note) note.textContent = PREVIEW;
  const race = document.querySelector("#feedback-race");
  if (race && !race.value.trim() && !editingId) {
    race.value = activeRaceName();
  }
  paintList();
  paintSendLink();
}

function saveFeedback() {
  const entry = formEntry();
  const saved = entries().filter((item) => item.id !== entry.id);
  saved.unshift(entry);
  const ok = writeJson(FEEDBACK_KEY, saved);
  editingId = entry.id;
  const status = document.querySelector("#feedback-status");
  if (status) {
    status.textContent = ok
      ? "Saved on this phone. You can review it offline."
      : "This browser blocked local storage, so the note stayed on screen only.";
  }
  paintList();
  paintSendLink();
}

export function setupFeedback() {
  document.querySelector("#feedback-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    saveFeedback();
  });
  document.querySelector("#feedback-save")?.addEventListener("click", saveFeedback);
  document.querySelector("#feedback-new")?.addEventListener("click", () => {
    fillForm(null);
    const race = document.querySelector("#feedback-race");
    if (race) race.value = activeRaceName();
    paintSendLink();
  });
  for (const node of document.querySelectorAll("#feedback-form input, #feedback-form textarea")) {
    node.addEventListener("input", paintSendLink);
    node.addEventListener("change", paintSendLink);
  }
  paintFeedback();
}
