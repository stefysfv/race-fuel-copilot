/** localStorage helpers. A private browser must not take the page down. */

export const INSTALL_DISMISS_KEY = "fuel-install-dismissed-at";
export const OFFLINE_VERIFIED_KEY = "fuel-offline-verified-at";
export const FIRST_OFFLINE_KEY = "fuel-first-offline";
export const FEEDBACK_KEY = "fuel-feedback";

export const DAY_MS = 24 * 60 * 60 * 1000;

export function readText(key) {
  try {
    return localStorage.getItem(key);
  } catch (error) {
    return null;
  }
}

export function writeText(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (error) {
    return false;
  }
}

export function readJson(key) {
  const raw = readText(key);
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch (error) {
    return null;
  }
}

export function writeJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch (error) {
    return false;
  }
}
