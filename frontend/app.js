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
import { renderLocker, addCustomItem, renderCustomAids, onAddAidClick, setFuelPreference, renderVestRows, paintDrinkControls, onVestInventoryInput, renderFoods, saveMenu, setFoodCarbs, toggleFood, onFoodEditorClick, onFoodEditorInput, addCustomFood, toggleFoodFlavor, loadStoredPreferences, renderGutPill, openGutModal, markGut, closeGutModal, saveGut, resetGut } from "./food_menu.js";
import { renderPacking, stepLeg, exportRaceCard, closeCrewModal } from "./packing.js";
import { renderLive, startClock, ensureTicker, logIntake, undoAutoLog, paintRaceControls, onClockTick, onPaintDesk } from "./live.js";
import { mapApi } from "./map_screen.js";
import { applyTargetDefault, paintAidDrawer, setSource, setCarbs, loadRaces } from "./plan_request.js";
import { raceCoachTick, preloadEnglishVoices, onAlertsClick, onTestAlert, requestWakeLock, bindLiveAlerts } from "./alerts.js";
import { onRaceModeClick, armGpsWatch, paintAutoGps, onUpdateGpsClick, triggerRescue, paintDesk } from "./gps.js";
import { showSetupPane, toggleSetup, showTab } from "./navigation.js";
import { buildPlan } from "./build_plan.js";

bindLiveAlerts(logIntake, paintRaceControls);
onClockTick(() => {
  if (state.raceMode || state.alerts) raceCoachTick();
});
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
}

function bind() {
  listen("#setup-form", "submit", (event) => buildPlan(event));
  listen("#source-race", "click", () => setSource("race"));
  listen("#source-gpx", "click", () => {
    setSource("gpx");
    ui.gpxFile.click();
  });
  ui.raceSelect?.addEventListener("change", () => {
    setSource("race");
    state.raceId = ui.raceSelect.value;
    applyTargetDefault();
  });
  listen("#add-aid", "click", onAddAidClick);
  listen("#edit-stations", "click", () => {
    state.editStations = !(state.source === "gpx" || state.editStations);
    if (state.source === "gpx") state.editStations = false;
    paintAidDrawer();
  });
  ui.gpxFile?.addEventListener("change", async () => {
    const file = ui.gpxFile.files?.[0];
    if (!file) {
      return;
    }
    state.gpxXml = await file.text();
    state.gpxName = file.name;
    ui.gpxName.textContent = `Loaded ${file.name}`;
    setSource("gpx");
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
  ui.rescue?.addEventListener("click", () => triggerRescue());
  ui.raceMode?.addEventListener("click", onRaceModeClick);
  ui.undoAuto?.addEventListener("click", () => undoAutoLog());
  ui.alerts?.addEventListener("click", () => onAlertsClick());
  ui.testAlert?.addEventListener("click", () => onTestAlert());
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.raceMode) {
      requestWakeLock();
    }
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
  ui.crewModal?.addEventListener("click", (event) => {
    if (event.target === ui.crewModal) {
      closeCrewModal();
    }
  });
  for (const button of document.querySelectorAll(".fuel-pref")) {
    button.addEventListener("click", () => setFuelPreference(button.dataset.preference, true));
  }
  ui.weight?.addEventListener("input", renderLocker);
}

async function init() {
  try {
    setupDiagnostics();
    watchReadiness();
    resetOutdatedStorage();
    preloadEnglishVoices();
    loadStoredPreferences();
    renderFoods();
    renderGutPill();
    bind();
    setCarbs(60);
    setSource("race");
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
      if (state.raceId && ui.raceSelect) {
        ui.raceSelect.value = state.raceId;
      }
      applyTargetDefault();
    } catch (error) {
      console.error(error);
      setStatus(error.message);
    }
    renderLocker();
    renderPacking();
    if (state.packedPlan) {
      showSetupPane("packing");
      state.autoGps = true;
      armGpsWatch();
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
      navigator.serviceWorker.register("/sw.js?v=21", { type: "module" }).then((registration) => {
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
