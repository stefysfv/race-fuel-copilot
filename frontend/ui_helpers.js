/** Small DOM and clock helpers shared by the tabs. */

import { state, ui } from "./state.js";

export let toastTimer = 0;

export function setStatus(message, hold = false) {
  const text = message || "";
  if (ui.status) ui.status.textContent = hold ? text : "";
  if (!ui.toast) return;
  window.clearTimeout(toastTimer);
  if (!text) {
    ui.toast.textContent = "";
    ui.toast.classList.add("hidden");
    return;
  }
  ui.toast.textContent = text;
  ui.toast.classList.remove("hidden");
  toastTimer = window.setTimeout(() => {
    ui.toast.classList.add("hidden");
  }, hold ? 12000 : 3000);
}

export function formatClock(minutes) {
  const sign = minutes < 0 ? "−" : "";
  const total = Math.abs(Math.round(minutes * 60));
  const hours = Math.floor(total / 3600);
  const mins = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  const body = `${mins}:${String(secs).padStart(2, "0")}`;
  return hours > 0 ? `${sign}${hours}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}` : `${sign}${body}`;
}

export function formatDuration(minutes) {
  const total = Math.round(minutes);
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours > 0 ? `${hours} h ${mins} min` : `${mins} min`;
}

export function elapsedMin() {
  if (state.sim.active) {
    return state.sim.elapsed;
  }
  if (!state.startedAt) {
    return 0;
  }
  return (Date.now() - state.startedAt) / 60000;
}

export function paintMinutes() {
  if (!ui.minutes) return;
  const parsed = Number(ui.minutes.value);
  if (!Number.isFinite(parsed)) return;
  const clamped = Math.min(59, Math.max(0, Math.round(parsed)));
  ui.minutes.value = String(clamped).padStart(2, "0");
}

export function deliveryRateLabel(plan) {
  const delivered = Math.round(plan.totals?.carbs_g_per_hour_delivered || 0);
  const target = Math.round(plan.athlete?.carbs_g_per_hour || 0);
  return `${delivered}/${target} g/h`;
}

export function grouped(value) {
  return Math.round(value).toLocaleString("en-US");
}

export function compactHours(minutes) {
  const total = Math.max(0, Math.round(minutes));
  const hours = Math.floor(total / 60);
  const mins = total % 60;
  return hours > 0 ? `${hours}h ${String(mins).padStart(2, "0")}m` : `${mins}m`;
}

export function wallElapsedMin() {
  if (!state.startedAt) {
    return 0;
  }
  return (Date.now() - state.startedAt) / 60000;
}

export function listen(selector, event, handler) {
  document.querySelector(selector)?.addEventListener(event, handler);
}

export function escapeHtml(value) {
  return escapeAttr(value);
}

export function escapeAttr(value) {
  return String(value).replace(/[&<>"']/g, (character) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[character]));
}

export function clampCarbs(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 1;
  return Math.min(120, Math.max(1, Math.round(number)));
}
