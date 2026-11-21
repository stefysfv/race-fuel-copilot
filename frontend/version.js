/** App version, in-app changelog, and "what's new" banner on update. */

import { readText, writeText } from "./local_store.js";

export const APP_VERSION = "1.0.0";
export const SEEN_VERSION_KEY = "fuel-seen-version";

export const CHANGELOG = [
  {
    version: "1.0.0",
    date: "2026-11-21",
    items: [
      "Added Privacy Policy, Terms & Not-Medical-Advice pages, MIT License, and in-app Changelog.",
      "Added opt-in Copy Diagnostics for bug reports (zero automatic error reporting).",
      "Added 12+ hour endurance features: GPS quality guard, adaptive polling, Battery Saver OLED mode, wake lock, and 48 px+ glove-friendly Race Mode buttons.",
      "Added Metric/Imperial units toggle and print/shareable Crew Sheet with +/-10% ETA bands.",
    ],
  },
];

export function setupVersionUi() {
  const versionBadge = document.querySelector("#app-version");
  if (versionBadge) versionBadge.textContent = `v${APP_VERSION}`;

  const modal = document.querySelector("#changelog-modal");
  const list = document.querySelector("#changelog-body");
  const openBtn = document.querySelector("#open-changelog");
  const closeBtn = document.querySelector("#close-changelog");
  const closeBottom = document.querySelector("#close-changelog-bottom");

  if (list) {
    list.replaceChildren();
    for (const release of CHANGELOG) {
      const h = document.createElement("h3");
      h.textContent = `v${release.version} (${release.date})`;
      const ul = document.createElement("ul");
      for (const line of release.items) {
        const li = document.createElement("li");
        li.textContent = line;
        ul.append(li);
      }
      list.append(h, ul);
    }
  }

  const openModal = () => modal?.classList.remove("hidden");
  const closeModal = () => modal?.classList.add("hidden");
  openBtn?.addEventListener("click", openModal);
  closeBtn?.addEventListener("click", closeModal);
  closeBottom?.addEventListener("click", closeModal);

  const banner = document.querySelector("#whats-new-banner");
  const bannerOpen = document.querySelector("#whats-new-open");
  const bannerDismiss = document.querySelector("#whats-new-dismiss");
  const seen = readText(SEEN_VERSION_KEY);

  if (seen && seen !== APP_VERSION && banner) {
    banner.classList.remove("hidden");
  } else if (!seen) {
    writeText(SEEN_VERSION_KEY, APP_VERSION);
  }

  bannerOpen?.addEventListener("click", () => {
    writeText(SEEN_VERSION_KEY, APP_VERSION);
    banner?.classList.add("hidden");
    openModal();
  });
  bannerDismiss?.addEventListener("click", () => {
    writeText(SEEN_VERSION_KEY, APP_VERSION);
    banner?.classList.add("hidden");
  });
}
