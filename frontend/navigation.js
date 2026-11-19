/** Setup panes and the setup / map / live tabs. */

import { paintFeedback } from "./feedback.js";
import { renderLive } from "./live.js";
import { drawMap, mapView } from "./map_screen.js";
import { paintReadiness } from "./offline_status.js";
import { setupSummaryLine } from "./packing.js";
import { state, ui } from "./state.js";

export function showSetupPane(pane) {
  const name = pane === "packing" || pane === "ready" ? pane : "configure";
  state.setupPane = name;
  ui.configurePane.classList.toggle("hidden", name !== "configure");
  ui.packingPane.classList.toggle("hidden", name !== "packing");
  ui.readyPane?.classList.toggle("hidden", name !== "ready");
  ui.paneConfigure.setAttribute("aria-selected", name === "configure" ? "true" : "false");
  ui.panePacking.setAttribute("aria-selected", name === "packing" ? "true" : "false");
  ui.paneReady?.setAttribute("aria-selected", name === "ready" ? "true" : "false");
  if (name === "configure") {
    ui.form?.classList.remove("hidden");
    if (ui.configurePane) ui.configurePane.scrollTop = 0;
  }
  if (name === "ready") {
    paintReadiness().catch((error) => console.error(error));
  }
}

export function collapseSetup() {
  if (state.packedPlan) {
    setupSummaryLine();
    showSetupPane("packing");
  }
}

export function showSetupForm() {
  showSetupPane("configure");
}

export function toggleSetup() {
  showSetupPane(state.setupPane === "packing" ? "configure" : "packing");
}

export function showTab(name) {
  const tabs = { setup: "tab-setup", map: "tab-map", live: "tab-live", feedback: "tab-feedback" };
  for (const [key, id] of Object.entries(tabs)) {
    document.querySelector(`#${id}`).classList.toggle("hidden", key !== name);
    const tabButton = document.querySelector(`#nav-${key}`);
    if (!tabButton) continue;
    if (key === name) {
      tabButton.setAttribute("aria-current", "page");
    } else {
      tabButton.removeAttribute("aria-current");
    }
  }
  document.body.dataset.tab = name;
  if (name === "map") {
    drawMap();
    setTimeout(() => mapView?.resize(), 150);
  }
  if (name === "live") {
    renderLive();
  }
  if (name === "feedback") {
    paintFeedback();
  }
}
