/** UI controller.

Wires the tabs and boots the page. Plan requests live in `build_plan.js`.
The sports math runs in the browser worker through `engine_client.js`.
*/

import { startEngineWhenIdle } from "./engine_client.js";
import { loadSession } from "./offline_db.js";
import { state, ui } from "./state.js";
import { setStatus, listen, paintMinutes } from "./ui_helpers.js";
import { setupDiagnostics, watchReadiness } from "./offline_status.js";
import { resetOutdatedStorage, restore } from "./session.js";
import { renderLocker, addCustomItem, renderCustomAids, onAddAidClick, setFuelPreference, renderVestRows, paintDrinkControls, onVestInventoryInput, renderFoods, saveMenu, setFoodCarbs, toggleFood, onFoodEditorClick, onFoodEditorInput, addCustomFood, toggleFoodFlavor, loadStoredPreferences, renderGutPill, openGutModal, markGut, closeGutModal, saveGut, resetGut, onFoodSearchInput, onFoodSuggestionClick } from "./food_menu.js";
import { renderPacking, setupSummaryLine, stepLeg, exportRaceCard, closeCrewModal } from "./packing.js";
import { renderLive, startClock, ensureTicker, logIntake, paintRaceControls, onPaintDesk, markLegCompleted, openRaceMenu, closeRaceMenu, openRecapModal, closeRecapModal, prefillFeedbackFromRecap } from "./live.js";
import { drawMap, mapApi } from "./map_screen.js";
import { applyTargetDefault, paintAidDrawer, paintUnitInputs, setSource, setCarbs, syncTempFromInput, syncWeightFromInput, loadRaces } from "./plan_request.js";
import { applyZonePreset, loadZoneProfile, onZoneSlider, scheduleZonePreview } from "./zone_profile.js";
import { onRaceModeClick, togglePauseRace, openRestartModal, closeRestartModal, confirmRestartRace, skipStartCountdown, paintAutoGps, onUpdateGpsClick, triggerRescue, paintDesk, loadBatterySaver, setBatterySaver, handleVisibilityChange } from "./gps.js";
import { showSetupPane, toggleSetup, showTab } from "./navigation.js";
import { buildPlan } from "./build_plan.js";
import { setupFeedback, paintFeedback } from "./feedback.js";
import { repaintGpxUnits, setupGpxUpload } from "./gpx_upload.js";
import { setupInstallBanner } from "./install_prompt.js";
import { setupVersionUi } from "./version.js";
import { getUnitSystem, onUnitChange, setUnitSystem } from "./units.js";

onPaintDesk(paintDesk);

function wireCriticalControls() {
  window.handleBuildPlan = buildPlan;
  const buildBtn = document.querySelector("#build-plan");
  if (buildBtn) {
    buildBtn.disabled = false;
    buildBtn.addEventListener("click", (event) => {
      event.preventDefault();
      buildPlan(event);
    });
  }
  document.querySelector("#pane-configure")?.addEventListener("click", () => showSetupPane("configure"));
  document.querySelector("#pane-packing")?.addEventListener("click", () => showSetupPane("packing"));
  document.querySelector("#pane-ready")?.addEventListener("click", () => showSetupPane("ready"));
  document.querySelector("#nav-setup")?.addEventListener("click", () => showTab("setup"));
  document.querySelector("#nav-map")?.addEventListener("click", () => showTab("map"));
  document.querySelector("#nav-live")?.addEventListener("click", () => showTab("live"));
  document.querySelector("#nav-feedback")?.addEventListener("click", () => showTab("feedback"));
  document.querySelector("#open-feedback")?.addEventListener("click", () => showTab("feedback"));
}

function bind() {
  listen("#setup-form", "submit", (event) => buildPlan(event));
  listen("#add-aid", "click", onAddAidClick);
  listen("#edit-stations", "click", () => {
    state.editStations = !(state.source === "gpx" || state.editStations);
    if (state.source === "gpx") state.editStations = false;
    paintAidDrawer();
  });
  for (const input of [ui.hours, ui.minutes]) {
    input?.addEventListener("input", () => {
      state.targetEdited = true;
    });
  }
  ui.minutes?.addEventListener("blur", paintMinutes);
  for (const button of document.querySelectorAll(".gut")) {
    button.addEventListener("click", () => setCarbs(Number(button.dataset.carbs)));
  }
  ui.countdown?.addEventListener("click", startClock);
  ui.log?.addEventListener("click", () => logIntake().catch((error) => setStatus(error.message)));
  ui.gps?.addEventListener("click", onUpdateGpsClick);
  ui.setupSummary?.addEventListener("click", toggleSetup);
  listen("#center-me", "click", () => {
    showTab("map");
    requestAnimationFrame(() => {
      if (!state.realGps) {
        setStatus("Tap Sync GPS on the Live tab to drop your real location on the map.");
        return;
      }
      try {
        const view = mapApi();
        view.setRealLocation(state.realGps.latitude, state.realGps.longitude);
        view.centerOnMe();
      } catch (error) {
        setStatus(error.message);
      }
    });
  });
  ui.rescue?.addEventListener("click", () => { closeRaceMenu(); triggerRescue(); });
  ui.liveMenuBtn?.addEventListener("click", openRaceMenu);
  ui.closeRaceMenu?.addEventListener("click", closeRaceMenu);
  ui.closeRaceMenuBottom?.addEventListener("click", closeRaceMenu);
  ui.raceMenuModal?.addEventListener("click", (event) => { if (event.target === ui.raceMenuModal) closeRaceMenu(); });
  ui.racePause?.addEventListener("click", () => { closeRaceMenu(); togglePauseRace(); });
  ui.raceRestart?.addEventListener("click", () => { closeRaceMenu(); openRestartModal(); });
  ui.sectionDoneCurrent?.addEventListener("click", () => {
    const btn = ui.sectionDoneCurrent;
    if (btn?.dataset.legDone) markLegCompleted(btn.dataset.legDone, Number(btn.dataset.legKm || 0), Number(btn.dataset.legPlanMin || 0));
  });
  ui.confirmRestart?.addEventListener("click", () => confirmRestartRace());
  ui.cancelRestart?.addEventListener("click", closeRestartModal);
  ui.closeRestart?.addEventListener("click", closeRestartModal);
  ui.restartModal?.addEventListener("click", (event) => { if (event.target === ui.restartModal) closeRestartModal(); });
  ui.raceCountdownSkip?.addEventListener("click", skipStartCountdown);
  ui.liveGoSetup?.addEventListener("click", () => showTab("setup"));
  ui.startRaceCta?.addEventListener("click", onRaceModeClick);
  ui.launchCrewBtn?.addEventListener("click", exportRaceCard);
  ui.liveCrewBtn?.addEventListener("click", exportRaceCard);
  ui.liveAheadList?.addEventListener("click", (event) => {
    const btn = event.target.closest?.("[data-leg-done]");
    if (btn) markLegCompleted(btn.dataset.legDone);
  });
  ui.openRecapBtn?.addEventListener("click", openRecapModal);
  ui.closeRecap?.addEventListener("click", closeRecapModal);
  ui.closeRecapBottom?.addEventListener("click", closeRecapModal);
  ui.recapModal?.addEventListener("click", (event) => { if (event.target === ui.recapModal) closeRecapModal(); });
  ui.recapToGut?.addEventListener("click", () => { closeRecapModal(); openGutModal(); });
  ui.recapToFeedback?.addEventListener("click", () => { prefillFeedbackFromRecap(); closeRecapModal(); showTab("feedback"); });
  document.addEventListener("visibilitychange", () => {
    handleVisibilityChange();
  });
  window.addEventListener("online", () => {
    if (state.packedPlan) drawMap();
  });
  window.addEventListener("offline", () => {
    if (state.packedPlan) drawMap();
  });
  listen("#add-locker", "click", addCustomItem);
  ui.exportCard?.addEventListener("click", exportRaceCard);
  document.querySelector("#leg-prev")?.addEventListener("click", () => stepLeg(-1));
  document.querySelector("#leg-next")?.addEventListener("click", () => stepLeg(1));
  document.querySelector("#leg-select")?.addEventListener("change", (event) => {
    const value = event.target.value;
    state.legView = value === "all" ? "all" : Number(value);
    renderPacking();
  });
  listen("#close-crew", "click", closeCrewModal);
  listen("#close-crew-bottom", "click", closeCrewModal);
  listen("#open-gut-live", "click", openGutModal);
  listen("#vest-custom-add", "click", () => addCustomFood("vest"));
  listen("#station-custom-add", "click", () => addCustomFood("station"));
  listen("#vest-custom-name", "input", () => onFoodSearchInput("vest"));
  listen("#station-custom-name", "input", () => onFoodSearchInput("station"));
  listen("#vest-custom-name", "focus", () => onFoodSearchInput("vest", true));
  listen("#station-custom-name", "focus", () => onFoodSearchInput("station", true));
  document.addEventListener("click", (event) => {
    if (!event.target.closest?.(".food-search-wrap")) {
      document.querySelector("#vest-suggestions")?.classList.add("hidden");
      document.querySelector("#station-suggestions")?.classList.add("hidden");
    }
  });
  listen("#vest-suggestions", "click", (event) => onFoodSuggestionClick(event, "vest"));
  listen("#station-suggestions", "click", (event) => onFoodSuggestionClick(event, "station"));
  listen("#vest-custom-flavor", "click", () => toggleFoodFlavor("vest"));
  listen("#station-custom-flavor", "click", () => toggleFoodFlavor("station"));
  document.querySelector("#vest-active")?.addEventListener("click", (event) => onFoodEditorClick(event, "vest"));
  document.querySelector("#station-active")?.addEventListener("click", (event) => onFoodEditorClick(event, "station"));
  document.querySelector("#vest-active")?.addEventListener("input", (event) => onFoodEditorInput(event, "vest"));
  document.querySelector("#station-active")?.addEventListener("input", (event) => onFoodEditorInput(event, "station"));
  listen("#close-gut", "click", closeGutModal);
  listen("#close-gut-bottom", "click", closeGutModal);
  listen("#save-gut", "click", saveGut);
  listen("#gut-reset", "click", resetGut);
  const vestFoods = document.querySelector("#vest-foods");
  if (vestFoods) {
    for (const type of ["click", "mousedown", "touchstart", "keydown"]) {
      vestFoods.addEventListener(type, (event) => {
        if (event.target.closest?.(".qty-input, .carbs-input")) event.stopPropagation();
      });
    }
    vestFoods.addEventListener("click", (event) => toggleFood(event, "vest"));
    vestFoods.addEventListener("input", onVestInventoryInput);
  }
  for (const button of document.querySelectorAll(".vest-tab")) {
    button.addEventListener("click", () => {
      state.vestTab = button.dataset.vestTab || "gels";
      renderVestRows();
    });
  }
  listen("#drink-toggle", "click", () => {
    if (state.vestFoods.has("drink_30")) state.vestFoods.delete("drink_30");
    else state.vestFoods.add("drink_30");
    paintDrinkControls();
    saveMenu();
  });
  listen("#salt-toggle", "click", () => {
    state.saltOn = !state.saltOn;
    if (state.saltOn && !(state.saltQty >= 1)) state.saltQty = 4;
    paintDrinkControls();
    saveMenu();
  });
  for (const [selector, key] of [
    ["#flask-qty", "flaskQty"],
    ["#drink-qty", "drinkScoops"],
    ["#salt-qty", "saltQty"],
    ["#salt-mg", "saltMg"],
  ]) {
    document.querySelector(selector)?.addEventListener("input", (event) => {
      event.stopPropagation();
      state[key] = parseInt(event.target.value, 10) || 0;
      saveMenu();
    });
  }
  document.querySelector("#drink-carbs")?.addEventListener("input", (event) => {
    event.stopPropagation();
    const parsed = parseInt(event.target.value, 10);
    if (parsed >= 1 && parsed <= 120) setFoodCarbs("vest", "drink_30", parsed);
    saveMenu();
  });
  document.querySelector("#station-foods")?.addEventListener("click", (event) => toggleFood(event, "station"));
  document.querySelector("#gut-modal")?.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-gut]");
    if (!button) return;
    markGut(button.dataset.gut, button.dataset.value);
  });
  document.querySelector("#zone-drawer")?.addEventListener("click", (event) => {
    const button = event.target.closest?.("[data-zone-preset]");
    if (!button) return;
    applyZonePreset(button.dataset.zonePreset);
  });
  for (const input of document.querySelectorAll("[data-zone-edge]")) {
    input.addEventListener("input", () => onZoneSlider(input.dataset.zoneEdge, input.value));
  }
  ui.crewModal?.addEventListener("click", (event) => {
    if (event.target === ui.crewModal) {
      closeCrewModal();
    }
  });
  for (const button of document.querySelectorAll(".fuel-pref")) {
    button.addEventListener("click", () => setFuelPreference(button.dataset.preference, true));
  }
  ui.weight?.addEventListener("input", () => {
    syncWeightFromInput();
    renderLocker();
  });
  ui.temperature?.addEventListener("input", syncTempFromInput);
  ui.startTime?.addEventListener("input", () => {
    if (/^\d{2}:\d{2}$/.test(ui.startTime.value)) {
      state.startTime = ui.startTime.value;
      persist();
    }
  });
  listen("#auto-aids", "click", () => {
    const totalKm = Number(state.distanceOverrideKm) || Number(state.gpxReport?.distance_km) || 0;
    if (!(totalKm >= 20)) {
      setStatus("Load a GPX longer than 20 km first, then auto-add stations.");
      return;
    }
    const step = totalKm >= 70 ? 15 : 12;
    let added = 0;
    for (let km = step; km <= totalKm - 8; km += step) {
      const rounded = Math.round(km);
      if (state.customAids.some((s) => Math.abs(Number(s.distance_km) - rounded) < 3)) continue;
      state.customAids.push({ name: `Aid ${added + 1}`, distance_km: rounded, has_water: true, has_food: true, drop_bag: false });
      added += 1;
    }
    renderCustomAids();
    persist();
    setStatus(added ? `Added ${added} food aid stations across ${totalKm.toFixed(0)} km. Rebuild plan to use them.` : "Aid stations are already spaced across this course.");
  });
  for (const button of document.querySelectorAll(".unit-btn")) {
    button.addEventListener("click", () => setUnitSystem(button.dataset.unit));
  }
  onUnitChange(() => {
    paintUnitInputs();
    repaintGpxUnits();
    renderCustomAids();
    if (state.packedPlan) {
      setupSummaryLine();
      renderPacking();
      renderLive();
      drawMap();
      if (!ui.crewModal?.classList.contains("hidden")) exportRaceCard();
    }
  });
}

async function init() {
  try {
    resetOutdatedStorage();
    setupInstallBanner();
    setupVersionUi();
    setupFeedback();
    setupDiagnostics();
    watchReadiness();
    getUnitSystem();
    loadBatterySaver();
    loadStoredPreferences();
    loadZoneProfile();
    renderFoods();
    renderGutPill();
    bind();
    setCarbs(60);
    setSource("gpx");
    try {
      const session = await loadSession();
      if (restore(session)) {
        renderCustomAids();
        setFuelPreference(state.fuelPreference);
        paintMinutes();
        ui.gpxName.textContent = state.gpxName ? `Loaded ${state.gpxName}` : "";
        setSource(state.source);
        setCarbs(state.carbs);
        if (state.giRescue) {
          ui.liveMessage.textContent = "GI Rescue is on for the rest of this plan.";
        }
      }
    } catch (error) {
      console.error(error);
      setStatus("Saved race data on this phone could not be read.");
    }
    try {
      await loadRaces();
      applyTargetDefault();
      scheduleZonePreview();
      paintFeedback();
    } catch (error) {
      console.error(error);
      setStatus(error.message);
    }
    paintUnitInputs();
    setupGpxUpload();
    renderLocker();
    renderPacking();
    if (state.packedPlan) {
      showSetupPane("packing");
    }
    renderLive();
    paintAutoGps();
    setInterval(paintAutoGps, 1000);
    ensureTicker();
    if ("serviceWorker" in navigator) {
      const hadWorker = Boolean(navigator.serviceWorker.controller);
      let reloaded = false;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        if (!hadWorker || reloaded) return;
        reloaded = true;
        window.location.reload();
      });
      navigator.serviceWorker.register("/sw.js?v=23", { type: "module" }).then((registration) => {
        registration.update().catch(() => {});
      }).catch(() => {});
    }
  } catch (error) {
    console.error(error);
  }
}

function boot() {
  try {
    wireCriticalControls();
  } catch (error) {
    console.error(error);
  }
  init();
  startEngineWhenIdle();
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", boot);
} else {
  boot();
}
