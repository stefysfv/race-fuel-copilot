/** App version, in-app changelog, and "what's new" banner on update. */

import { readText, writeText } from "./local_store.js";

export const APP_VERSION = "1.0.0";
export const SEEN_VERSION_KEY = "fuel-seen-version";

export const CHANGELOG = [
  {
    version: APP_VERSION,
    date: "2026-11-24",
    items: [
      "Load a GPX, build a packing plan and share station handovers with your crew.",
      "Live shows your next intake first. Tap Log intake for food or drink and Sync GPS to update the remaining plan.",
      "Completed sections show their course distances and survive reload. The finish recap and feedback form identify your course.",
    ],
  },
  {
    version: "0.10.0-beta",
    date: "2026-11-22",
    items: [
      "New check-in concept for 12+ hour races: keep your phone locked in your vest. At an aid station, or when you slow down, pull it out for about 10 seconds, tap Sync GPS, and the rest of your race is recalculated: arrival times and fueling windows.",
      "GPS is read only when you tap Sync GPS or GI Rescue. No background tracking, no screen wake lock, and no voice, chime or notification cues. The app will not remind you to eat, so keep your own schedule.",
      "Off course: more than 2 km from the course, Live shows Off course and hides grade, zone and expected arrival instead of planned values.",
      "Finished sections clear from Live so you only see what is left ahead. Finish Run shows everything you ate.",
      "Fixed GI Rescue ignoring your GPS fix. Text and number fields no longer autocorrect or autofill.",
    ],
  },
  {
    version: "0.9.0-beta",
    date: "2026-11-21",
    items: [
      "Added Privacy Policy, Terms & Not-Medical-Advice pages, MIT License, and in-app Changelog.",
      "Added opt-in Copy Diagnostics for bug reports (zero automatic error reporting).",
      "Added long-ultra battery features (field battery test pending): GPS quality guard, adaptive polling, Battery Saver OLED mode, wake lock, and 48 px+ glove-friendly Race Mode buttons.",
      "Added Metric/Imperial units toggle and print/shareable Crew Sheet with +/-10% ETA bands.",
    ],
  },
];

export function setupVersionUi() {
  for (const badge of document.querySelectorAll("#app-version, #update-version")) {
    badge.textContent = `v${APP_VERSION}`;
  }

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

  const infoModal = document.querySelector("#info-modal");
  const openInfoBtn = document.querySelector("#open-info");
  const closeInfoBtn = document.querySelector("#close-info");
  const closeInfoBottom = document.querySelector("#close-info-bottom");

  const openInfo = () => infoModal?.classList.remove("hidden");
  const closeInfo = () => infoModal?.classList.add("hidden");
  openInfoBtn?.addEventListener("click", openInfo);
  closeInfoBtn?.addEventListener("click", closeInfo);
  closeInfoBottom?.addEventListener("click", closeInfo);

  const openModal = () => {
    closeInfo();
    modal?.classList.remove("hidden");
  };
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
