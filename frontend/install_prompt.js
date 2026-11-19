/** Home Screen install card. Dismissal lasts seven days. */

import { DAY_MS, INSTALL_DISMISS_KEY, readText, writeText } from "./local_store.js";

const DISMISS_MS = 7 * DAY_MS;

export function isInstalled() {
  const media = window.matchMedia?.("(display-mode: standalone)");
  return window.navigator.standalone === true || Boolean(media?.matches);
}

function platform() {
  const ua = navigator.userAgent || "";
  const appleTouch = navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  if (/iPad|iPhone|iPod/.test(ua) || appleTouch) return "ios";
  if (/Android/i.test(ua)) return "android";
  return "other";
}

function steps() {
  if (platform() === "ios") {
    return "On iPhone or iPad, open this page in Safari and tap Share > Add to Home Screen.";
  }
  if (platform() === "android") {
    return "On Android, open the Chrome menu and tap Install app.";
  }
  return "Use the browser menu to install this site, or add it to your Home Screen.";
}

function dismissedRecently() {
  const at = Number(readText(INSTALL_DISMISS_KEY));
  if (!Number.isFinite(at)) return false;
  return Date.now() - at < DISMISS_MS;
}

export function paintInstallBanner() {
  const card = document.querySelector("#install-banner");
  if (!card) return;
  const show = !isInstalled() && !dismissedRecently();
  card.classList.toggle("hidden", !show);
  const copy = document.querySelector("#install-steps");
  if (copy && show) copy.textContent = steps();
}

export function setupInstallBanner() {
  document.querySelector("#install-dismiss")?.addEventListener("click", () => {
    writeText(INSTALL_DISMISS_KEY, String(Date.now()));
    paintInstallBanner();
  });
  paintInstallBanner();
}
