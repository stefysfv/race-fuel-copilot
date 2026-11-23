/** Vest menu, station menu, gut check, and the product locker. */

import { PRESETS, state, ui } from "./state.js";
import { clampCarbs, escapeAttr, setStatus } from "./ui_helpers.js";
import { persist } from "./session.js";
import { distanceFromDisplay, formatDistance } from "./units.js";

export function renderLocker() {
  const presets = document.querySelector("#locker-presets");
  const list = document.querySelector("#locker-list");
  if (!presets || !list) return;
  presets.replaceChildren();
  for (const preset of PRESETS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pill liquid-pill";
    const selected = state.locker.some((item) => item.name === preset.name && item.carbs_g === preset.carbs_g && item.caffeine_mg === preset.caffeine_mg);
    if (selected) {
      button.setAttribute("aria-checked", "true");
    }
    const dose = preset.kind === "liquid" ? `${preset.carbs_g} g/500 ml` : `${preset.carbs_g} g`;
    const caffeine = preset.caffeine_mg ? ` + ${preset.caffeine_mg} mg` : "";
    button.textContent = `${preset.name} ${dose}${caffeine}`;
    button.addEventListener("click", () => {
      if (selected) {
        state.locker = state.locker.filter((item) => item.name !== preset.name);
      } else if (state.locker.length < 12) {
        state.locker.push({ ...preset });
      }
      renderLocker();
    });
    presets.append(button);
  }
  list.replaceChildren();
  for (const item of state.locker) {
    const row = document.createElement("li");
    row.className = "locker-row";
    const label = document.createElement("span");
    label.textContent = `${item.name} · ${item.carbs_g} g · ${item.caffeine_mg} mg caffeine`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "Remove";
    remove.addEventListener("click", () => {
      state.locker = state.locker.filter((entry) => entry !== item);
      renderLocker();
    });
    row.append(label, remove);
    list.append(row);
  }
  const weight = Number(state.weightKg);
  const cap = Number.isFinite(weight) ? Math.round(weight * 5) : 0;
  const capLine = document.querySelector("#caffeine-cap");
  if (capLine) capLine.textContent = `Cap ${cap} mg`;
}

export function addCustomItem() {
  const name = document.querySelector("#custom-name").value.trim();
  const carbs = Number(document.querySelector("#custom-carbs").value);
  const caffeine = Number(document.querySelector("#custom-caffeine").value || 0);
  if (!name || !(carbs > 0 && carbs <= 120) || !(caffeine >= 0 && caffeine <= 400)) {
    setStatus("A custom item needs a name, 1–120 g of carbs, and 0–400 mg of caffeine.");
    return;
  }
  if (state.locker.length >= 12) {
    setStatus("The locker holds 12 products.");
    return;
  }
  state.locker.push({ name, carbs_g: carbs, caffeine_mg: caffeine, kind: "gel" });
  document.querySelector("#custom-name").value = "";
  document.querySelector("#custom-carbs").value = "";
  document.querySelector("#custom-caffeine").value = "";
  renderLocker();
}

export function renderCustomAids() {
  ui.aidList.replaceChildren();
  for (const [index, station] of state.customAids.entries()) {
    const chip = document.createElement("li");
    chip.className = "pill liquid-pill aid-chip";
    const food = station.has_food ? " · Food" : "";
    const label = document.createElement("span");
    label.textContent = `${station.name} · ${formatDistance(station.distance_km, { digits: 1, compact: true })}${food}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "✕";
    remove.setAttribute("aria-label", `Remove ${station.name}`);
    remove.addEventListener("click", () => {
      state.customAids.splice(index, 1);
      renderCustomAids();
      persist();
    });
    chip.append(label, remove);
    ui.aidList.append(chip);
  }
}

export function addCustomAid({ name, distanceKm, hasFood, cutoffHours }) {
  const clean = name.trim();
  if (!clean) {
    setStatus("Name the aid station.");
    return;
  }
  if (!Number.isFinite(distanceKm) || distanceKm < 0) {
    setStatus("Enter the station kilometer.");
    return;
  }
  const station = {
    name: clean,
    distance_km: Math.round(distanceKm * 1000) / 1000,
    has_water: true,
    has_food: Boolean(hasFood),
    cutoff_hours: cutoffHours > 0 ? cutoffHours : null,
  };
  const existing = state.customAids.findIndex((item) => item.name.toLowerCase() === clean.toLowerCase());
  if (existing >= 0) {
    state.customAids[existing] = station;
  } else {
    state.customAids.push(station);
  }
  renderCustomAids();
  ui.aidDrawer.open = true;
  setStatus(`${clean} added at ${formatDistance(station.distance_km, { digits: 1 })}. Build the plan to pack it.`);
  persist();
}

export function onAddAidClick() {
  const cutoff = Number(ui.aidCutoff.value);
  addCustomAid({
    name: ui.aidName.value,
    distanceKm: distanceFromDisplay(Number(ui.aidKm.value)),
    hasFood: ui.aidFood.checked,
    cutoffHours: Number.isFinite(cutoff) ? cutoff : null,
  });
  ui.aidName.value = "";
  ui.aidKm.value = "";
  ui.aidCutoff.value = "";
  ui.aidFood.checked = true;
}

export function setFuelPreference(value, applyMenu = false) {
  state.fuelPreference = value || "gels_first";
  for (const button of document.querySelectorAll(".fuel-pref")) {
    if (button.dataset.preference === state.fuelPreference) {
      button.setAttribute("aria-checked", "true");
    } else {
      button.removeAttribute("aria-checked");
    }
  }
  if (applyMenu) applyStyleMenu(state.fuelPreference);
}

export function applyStyleMenu(value) {
  if (value === "real_food_heavy") {
    for (const key of ["gel_25", "iso_gel_22", "high_carb_gel", "caffeine_gel"]) state.vestFoods.delete(key);
    state.vestFoods.add("drink_30");
    state.vestFoods.add("bar");
    state.vestFoods.add("dates");
    state.vestFoods.add("banana");
    state.vestFoods.add("sandwich");
  } else if (value === "balanced_real_food") {
    state.vestFoods.add("gel_25");
    state.vestFoods.add("drink_30");
    state.vestFoods.add("banana");
    state.vestFoods.add("sandwich");
    state.vestFoods.add("dates");
  } else if (value === "gels_first") {
    state.vestFoods.add("gel_25");
    state.vestFoods.add("iso_gel_22");
    state.vestFoods.delete("sandwich");
    state.vestFoods.delete("bar");
  }
  renderFoods();
  saveMenu();
}

export const GEL_MENU = [
  { key: "gel_25", label: "Standard Gel", carbs: 25, sodium: 50, flavor: "Sweet", caffeine: 0, form: "gel" },
  { key: "iso_gel_22", label: "Isotonic Gel", carbs: 22, sodium: 40, flavor: "Sweet", caffeine: 0, form: "gel" },
  { key: "high_carb_gel", label: "High-Carb Gel", carbs: 40, sodium: 80, flavor: "Sweet", caffeine: 0, form: "gel" },
  { key: "caffeine_gel", label: "Caffeine Gel", carbs: 25, sodium: 50, flavor: "Sweet", caffeine: 100, form: "gel" },
];

export const SOLID_MENU = [
  { key: "sandwich", label: "Half Sandwich / Wrap", carbs: 25, sodium: 140, flavor: "Savoury/Salty", caffeine: 0, form: "solid" },
  { key: "bar", label: "Oat / Energy Bar", carbs: 30, sodium: 100, flavor: "Sweet", caffeine: 0, form: "solid" },
  { key: "dates", label: "Medjool Dates", carbs: 25, sodium: 10, flavor: "Sweet", caffeine: 0, form: "solid" },
  { key: "banana", label: "Banana", carbs: 22, sodium: 1, flavor: "Sweet", caffeine: 0, form: "solid" },
  { key: "chocolate", label: "Chocolate", carbs: 20, sodium: 20, flavor: "Sweet", caffeine: 0, form: "solid" },
  { key: "chips", label: "Salted Chips / Pretzels", carbs: 20, sodium: 320, flavor: "Savoury/Salty", caffeine: 0, form: "solid" },
];

export const DRINK_MENU = [
  { key: "drink_30", label: "Drink Mix", carbs: 30, sodium: 250, flavor: "Sweet", caffeine: 0, form: "liquid" },
];

export const VEST_MENU = [...GEL_MENU, ...DRINK_MENU, ...SOLID_MENU];

export const STATION_MENU = [
  { key: "sandwich", label: "Sandwich", carbs: 30, sodium: 250, flavor: "Savoury/Salty" },
  { key: "watermelon", label: "Watermelon", carbs: 12, sodium: 5, flavor: "Sweet" },
  { key: "cucumber", label: "Cucumber", carbs: 3, sodium: 300, flavor: "Savoury/Salty" },
  { key: "chips", label: "Chips", carbs: 22, sodium: 300, flavor: "Savoury/Salty" },
  { key: "chocolate", label: "Chocolate", carbs: 24, sodium: 20, flavor: "Sweet" },
  { key: "apple", label: "Apple", carbs: 18, sodium: 2, flavor: "Sweet" },
  { key: "dates", label: "Dates", carbs: 28, sodium: 5, flavor: "Sweet" },
  { key: "banana", label: "Banana", carbs: 22, sodium: 1, flavor: "Sweet" },
  { key: "cola_broth", label: "Cola or broth", carbs: 18, sodium: 200, flavor: "Sweet" },
];

export function menuFor(which) {
  return which === "vest" ? VEST_MENU : STATION_MENU;
}

export function selectedFoods(which) {
  const bag = which === "vest" ? state.vestFoods : state.stationFoods;
  return bag instanceof Set ? bag : new Set();
}

export function catalogFood(which, key) {
  return menuFor(which).find((item) => item.key === key) || null;
}

export function customFood(which, key) {
  return state.customFoods[which].find((item) => item.key === key) || null;
}

export function foodRecord(which, key) {
  const custom = customFood(which, key);
  if (custom) return custom;
  const base = catalogFood(which, key);
  if (!base) return null;
  const edit = state.foodEdits[which][key];
  return {
    key,
    name: (edit?.name || base.label).trim() || base.label,
    carbs_g: edit?.carbs_g ?? base.carbs,
    sodium_mg: base.sodium,
    flavor: base.flavor,
  };
}

export function customFoodPayload(which) {
  const items = [];
  for (const food of menuFor(which)) {
    if (!selectedFoods(which).has(food.key)) continue;
    const record = foodRecord(which, food.key);
    if (!record?.name) continue;
    items.push({
      key: String(food.key).slice(0, 40),
      name: String(record.name).trim().slice(0, 60),
      carbs_g: clampCarbs(record.carbs_g),
      sodium_mg: food.sodium,
      flavor: food.flavor,
    });
  }
  const extras = Array.isArray(state.customFoods?.[which]) ? state.customFoods[which] : [];
  for (const food of extras) {
    const key = String(food?.key || "").trim().slice(0, 40);
    if (!key || !selectedFoods(which).has(food.key)) continue;
    const name = String(food.name || "").trim();
    if (!name) continue;
    items.push({
      key,
      name: name.slice(0, 60),
      carbs_g: clampCarbs(food.carbs_g),
      sodium_mg: 0,
      flavor: food.flavor === "Savoury/Salty" ? "Savoury/Salty" : "Sweet",
    });
  }
  return items;
}

export function foodChipText(which, key) {
  const record = foodRecord(which, key);
  if (!record) return key;
  return `${record.name} · ${clampCarbs(record.carbs_g)}g`;
}

export function vestForm(key, name) {
  const catalog = VEST_MENU.find((item) => item.key === key);
  if (catalog?.form) return catalog.form;
  if (/gel/i.test(name)) return "gel";
  if (/drink|mix|cola|broth/i.test(name)) return "liquid";
  return "solid";
}

export function vestFlavor(flavor) {
  if (flavor === "Savoury/Salty" || flavor === "savoury") return "savoury";
  if (flavor === "neutral") return "neutral";
  return "sweet";
}

export function shownQty(key) {
  if (state.vestQty[key] == null) return 1;
  return state.vestQty[key];
}

export function packedVestPayload() {
  try {
    const selected = state.vestFoods instanceof Set ? state.vestFoods : new Set();
    const customs = Array.isArray(state.customFoods?.vest) ? state.customFoods.vest : [];
    const foods = [
      ...VEST_MENU.map((food) => ({ key: food.key, sodium: food.sodium, flavor: food.flavor })),
      ...customs.map((food) => ({ key: food?.key, sodium: 0, flavor: food?.flavor })),
    ];
    const items = [];
    for (const food of foods) {
      if (!food?.key || !selected.has(food.key)) continue;
      const record = foodRecord("vest", food.key);
      const name = String(record?.name || "").trim().slice(0, 60);
      const id = String(food.key).trim().slice(0, 40);
      if (!name || !id) continue;
      const rawQty = food.key === "drink_30"
        ? (state.drinkScoops ?? 1)
        : (state.vestQty?.[food.key] ?? 1);
      const qty = Math.min(30, Math.max(0, parseInt(rawQty, 10) || 0));
      if (qty < 1) continue;
      items.push({
        id,
        name,
        carbs_g: clampCarbs(record.carbs_g),
        sodium_mg: Math.min(2000, Math.max(0, Number(food.sodium) || 50)),
        caffeine_mg: Math.min(400, Math.max(0, Number(catalogFood("vest", food.key)?.caffeine) || 0)),
        qty,
        flavor: vestFlavor(food.flavor),
        form: vestForm(food.key, name),
      });
      if (items.length >= 24) break;
    }
    return items;
  } catch {
    return [];
  }
}

export function vestInventoryRows() {
  const catalog = state.vestTab === "solids" ? SOLID_MENU : GEL_MENU;
  const form = state.vestTab === "solids" ? "solid" : "gel";
  const foods = [
    ...catalog.map((food) => food.key),
    ...state.customFoods.vest.filter((food) => (food.form || "solid") === form).map((food) => food.key),
  ];
  return foods.map((key) => {
    const record = foodRecord("vest", key);
    if (!record) return "";
    const on = state.vestFoods.has(key);
    const name = escapeAttr(record.name);
    const fields = on
      ? `<span class="vest-fields">
          <label class="vest-field">Qty
            <input class="qty-input" type="number" inputmode="numeric" min="1" max="30" step="1" value="${shownQty(key)}" data-qty="${escapeAttr(key)}" aria-label="Quantity of ${name}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
          </label>
          <label class="vest-field">Carbs (g)
            <input class="carbs-input" type="number" inputmode="numeric" min="1" max="120" step="1" value="${clampCarbs(record.carbs_g)}" data-carbs="${escapeAttr(key)}" aria-label="Carbs in grams for ${name}" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
          </label>
        </span>`
      : "";
    return `<div class="vest-row">
      <button type="button" class="vest-toggle" data-food="${escapeAttr(key)}" aria-pressed="${on ? "true" : "false"}">${on ? "✓ " : ""}${name}</button>
      ${fields}
    </div>`;
  }).join("");
}

export function renderVestRows() {
  const drinks = state.vestTab === "drinks";
  document.querySelector("#vest-foods")?.classList.toggle("hidden", drinks);
  document.querySelector("#vest-drinks")?.classList.toggle("hidden", !drinks);
  document.querySelector("#vest-custom-row")?.classList.toggle("hidden", drinks);
  const name = document.querySelector("#vest-custom-name");
  if (name) name.placeholder = state.vestTab === "solids" ? "Custom solid food" : "Custom gel";
  for (const button of document.querySelectorAll(".vest-tab")) {
    button.setAttribute("aria-selected", button.dataset.vestTab === state.vestTab ? "true" : "false");
  }
  const vest = document.querySelector("#vest-foods");
  if (vest && !drinks) vest.innerHTML = vestInventoryRows();
  paintDrinkControls();
}

export function paintDrinkControls() {
  const drinkOn = state.vestFoods.has("drink_30");
  const drinkButton = document.querySelector("#drink-toggle");
  if (drinkButton) {
    drinkButton.setAttribute("aria-pressed", drinkOn ? "true" : "false");
    drinkButton.textContent = `${drinkOn ? "✓ " : ""}Carb Drink Mix in Flasks`;
  }
  const saltButton = document.querySelector("#salt-toggle");
  if (saltButton) {
    saltButton.setAttribute("aria-pressed", state.saltOn ? "true" : "false");
    saltButton.textContent = `${state.saltOn ? "✓ " : ""}Salt Capsules (optional)`;
  }
  document.querySelector("#salt-fields")?.classList.toggle("hidden", !state.saltOn);
  const flask = document.querySelector("#flask-qty");
  const scoops = document.querySelector("#drink-qty");
  const carbs = document.querySelector("#drink-carbs");
  const saltQty = document.querySelector("#salt-qty");
  const saltMg = document.querySelector("#salt-mg");
  if (flask && document.activeElement !== flask) flask.value = String(state.flaskQty);
  if (scoops && document.activeElement !== scoops) scoops.value = String(state.drinkScoops);
  if (carbs && document.activeElement !== carbs) carbs.value = String(clampCarbs(foodRecord("vest", "drink_30")?.carbs_g ?? 30));
  if (saltQty && document.activeElement !== saltQty) saltQty.value = String(state.saltQty);
  if (saltMg && document.activeElement !== saltMg) saltMg.value = String(state.saltMg);
}

export function onVestInventoryInput(event) {
  const qty = event.target.closest?.(".qty-input");
  if (qty) {
    state.vestQty[qty.dataset.qty] = parseInt(qty.value, 10) || 0;
    saveMenu();
    return;
  }
  const carbs = event.target.closest?.(".carbs-input");
  if (!carbs) return;
  const parsed = parseInt(carbs.value, 10);
  if (parsed >= 1 && parsed <= 120) setFoodCarbs("vest", carbs.dataset.carbs, parsed);
  saveMenu();
}

export function foodChips(which) {
  const selected = selectedFoods(which);
  const catalog = menuFor(which).map((food) => {
    const pressed = selected.has(food.key) ? "true" : "false";
    return `<button type="button" class="liquid-pill" data-food="${food.key}" aria-pressed="${pressed}">${escapeAttr(foodChipText(which, food.key))}</button>`;
  });
  const extras = state.customFoods[which].map((food) => {
    const pressed = selected.has(food.key) ? "true" : "false";
    return `<button type="button" class="liquid-pill" data-food="${escapeAttr(food.key)}" aria-pressed="${pressed}">${escapeAttr(foodChipText(which, food.key))}</button>`;
  });
  return catalog.concat(extras).join("");
}

export function editorRow(which, key) {
  const record = foodRecord(which, key);
  if (!record) return "";
  return `<div class="food-edit" data-edit="${which}" data-key="${escapeAttr(key)}">
    <input data-name type="text" maxlength="60" value="${escapeAttr(record.name)}" aria-label="Food name" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
    <span class="carb-step">
      <button type="button" data-step="-1" aria-label="Fewer carbs">−</button>
      <input data-carbs type="number" inputmode="decimal" min="1" max="120" step="1" value="${clampCarbs(record.carbs_g)}" aria-label="Carbs in grams" autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
      <span>g</span>
      <button type="button" data-step="1" aria-label="More carbs">+</button>
    </span>
  </div>`;
}

export function renderFoods() {
  renderVestRows();
  const station = document.querySelector("#station-foods");
  if (station) station.innerHTML = foodChips("station");
  renderActiveFoods();
}

export function renderActiveFoods() {
  const root = document.querySelector("#station-active");
  if (!root) return;
  const keys = [
    ...menuFor("station").map((food) => food.key),
    ...state.customFoods.station.map((food) => food.key),
  ].filter((key) => state.stationFoods.has(key));
  root.innerHTML = keys.map((key) => editorRow("station", key)).join("");
}

export function saveMenu() {
  try {
    localStorage.setItem("fuel-menu", JSON.stringify({
      vest: [...state.vestFoods],
      station: [...state.stationFoods],
      edits: state.foodEdits,
      custom: state.customFoods,
      qty: state.vestQty,
      flaskQty: state.flaskQty,
      drinkScoops: state.drinkScoops,
      saltOn: state.saltOn,
      saltMg: state.saltMg,
      saltQty: state.saltQty,
    }));
  } catch {
    /* private mode can block storage */
  }
}

export function setFoodName(which, key, name) {
  const custom = customFood(which, key);
  if (custom) {
    custom.name = name;
    return;
  }
  const base = catalogFood(which, key);
  if (!base) return;
  const previous = state.foodEdits[which][key];
  state.foodEdits[which][key] = {
    name,
    carbs_g: previous?.carbs_g ?? base.carbs,
  };
}

export function setFoodCarbs(which, key, carbs) {
  const next = clampCarbs(carbs);
  const custom = customFood(which, key);
  if (custom) {
    custom.carbs_g = next;
    return next;
  }
  const base = catalogFood(which, key);
  if (!base) return next;
  const previous = state.foodEdits[which][key];
  state.foodEdits[which][key] = {
    name: previous?.name || base.label,
    carbs_g: next,
  };
  return next;
}

export function paintChipLabel(which, key) {
  const root = document.querySelector(which === "vest" ? "#vest-foods" : "#station-foods");
  const button = root?.querySelector(`[data-food="${CSS.escape(key)}"]`);
  if (button) button.textContent = foodChipText(which, key);
}

export function toggleFood(event, which) {
  if (event.target.closest?.(".qty-input, .carbs-input")) return;
  const button = event.target.closest?.("[data-food]");
  if (!button) return;
  const bag = selectedFoods(which);
  const key = button.dataset.food;
  if (bag.has(key)) bag.delete(key);
  else bag.add(key);
  if (which === "vest") renderVestRows();
  else {
    button.setAttribute("aria-pressed", bag.has(key) ? "true" : "false");
    renderActiveFoods();
  }
  saveMenu();
}

export function onFoodEditorClick(event, which) {
  const step = event.target.closest?.("[data-step]");
  if (!step) return;
  const row = step.closest("[data-edit]");
  if (!row || row.dataset.edit !== which) return;
  const key = row.dataset.key;
  const record = foodRecord(which, key);
  if (!record) return;
  const next = setFoodCarbs(which, key, Number(record.carbs_g) + Number(step.dataset.step));
  const input = row.querySelector("[data-carbs]");
  if (input) input.value = String(next);
  paintChipLabel(which, key);
  saveMenu();
}

export function onFoodEditorInput(event, which) {
  const row = event.target.closest?.("[data-edit]");
  if (!row || row.dataset.edit !== which) return;
  const key = row.dataset.key;
  if (event.target.matches("[data-name]")) setFoodName(which, key, event.target.value);
  if (event.target.matches("[data-carbs]")) setFoodCarbs(which, key, event.target.value);
  paintChipLabel(which, key);
  saveMenu();
}

export function addCustomFood(which) {
  const nameInput = document.querySelector(which === "vest" ? "#vest-custom-name" : "#station-custom-name");
  const carbInput = document.querySelector(which === "vest" ? "#vest-custom-carbs" : "#station-custom-carbs");
  const flavorButton = document.querySelector(which === "vest" ? "#vest-custom-flavor" : "#station-custom-flavor");
  const name = nameInput?.value.trim() || "";
  const carbs = Number(carbInput?.value);
  if (!name || !(carbs >= 1 && carbs <= 120)) {
    setStatus("A custom food needs a name and 1–120 g of carbs.");
    return;
  }
  if (state.customFoods[which].length >= 12) {
    setStatus("That menu already has 12 custom foods.");
    return;
  }
  const key = `custom-${which}-${Date.now()}`;
  state.customFoods[which].push({
    key,
    name,
    carbs_g: clampCarbs(carbs),
    flavor: flavorButton?.dataset.flavor === "Savoury/Salty" ? "Savoury/Salty" : "Sweet",
    form: which === "vest" && state.vestTab === "gels" ? "gel" : "solid",
  });
  selectedFoods(which).add(key);
  if (nameInput) nameInput.value = "";
  if (carbInput) carbInput.value = "";
  renderFoods();
  saveMenu();
}

export function toggleFoodFlavor(which) {
  const button = document.querySelector(which === "vest" ? "#vest-custom-flavor" : "#station-custom-flavor");
  if (!button) return;
  const savoury = button.dataset.flavor !== "Savoury/Salty";
  button.dataset.flavor = savoury ? "Savoury/Salty" : "Sweet";
  button.textContent = savoury ? "Savoury" : "Sweet";
}

export function loadStoredPreferences() {
  try {
    const gut = JSON.parse(localStorage.getItem("fuel-gut") || "null");
    if (gut && typeof gut.carb_multiplier === "number") {
      state.gut = {
        carb_multiplier: Math.min(1.25, Math.max(0.75, gut.carb_multiplier)),
        climb_sensitivity: gut.climb_sensitivity === "sensitive" ? "sensitive" : "normal",
        early_savoury: Boolean(gut.early_savoury),
        saved: true,
      };
    }
    const menu = JSON.parse(localStorage.getItem("fuel-menu") || "null");
    if (Array.isArray(menu?.vest)) state.vestFoods = new Set(menu.vest.filter((key) => typeof key === "string"));
    if (Number.isFinite(Number(menu?.flaskQty))) state.flaskQty = Math.min(6, Math.max(0, Number(menu.flaskQty)));
    if (Number.isFinite(Number(menu?.drinkScoops))) state.drinkScoops = Math.min(30, Math.max(1, Number(menu.drinkScoops)));
    if (typeof menu?.saltOn === "boolean") state.saltOn = menu.saltOn;
    if (Number.isFinite(Number(menu?.saltMg))) state.saltMg = Math.min(2000, Math.max(1, Number(menu.saltMg)));
    if (Number.isFinite(Number(menu?.saltQty))) state.saltQty = Math.min(40, Math.max(0, Number(menu.saltQty)));
    if (Array.isArray(menu?.station)) state.stationFoods = new Set(menu.station.filter((key) => typeof key === "string"));
    for (const which of ["vest", "station"]) {
      const edits = menu?.edits?.[which];
      if (edits && typeof edits === "object") {
        state.foodEdits[which] = {};
        for (const [key, value] of Object.entries(edits)) {
          if (!value || typeof value.name !== "string") continue;
          state.foodEdits[which][key] = { name: value.name, carbs_g: clampCarbs(value.carbs_g) };
        }
      }
      if (menu?.qty && typeof menu.qty === "object") {
        state.vestQty = {};
        for (const [key, value] of Object.entries(menu.qty)) {
          const parsed = parseInt(value, 10);
          if (Number.isFinite(parsed)) state.vestQty[key] = Math.min(30, Math.max(0, parsed));
        }
      }
      if (Array.isArray(menu?.custom?.[which])) {
        state.customFoods[which] = menu.custom[which]
          .filter((item) => item && typeof item.key === "string" && typeof item.name === "string")
          .slice(0, 12)
          .map((item) => ({
            key: item.key,
            name: item.name,
            carbs_g: clampCarbs(item.carbs_g),
            flavor: item.flavor === "Savoury/Salty" ? "Savoury/Salty" : "Sweet",
            form: item.form === "gel" ? "gel" : "solid",
          }));
      }
    }
  } catch {
    /* keep the defaults */
  }
}

export function renderGutPill() {
  const pill = document.querySelector("#gut-pill");
  const text = document.querySelector("#gut-pill-text");
  if (!pill || !text) return;
  if (!state.gut.saved) {
    pill.classList.add("hidden");
    return;
  }
  const rate = Math.round(state.carbs * state.gut.carb_multiplier);
  const parts = [`Gut Calibrated: ${rate}g/h`];
  if (state.gut.early_savoury) parts.push("Early Savoury");
  if (state.gut.climb_sensitivity === "sensitive") parts.push("Strict Climbs");
  text.textContent = parts.join(" · ");
  pill.classList.remove("hidden");
}

export function openGutModal() {
  const modal = document.querySelector("#gut-modal");
  modal?.classList.remove("hidden");
  const gut = state.gut;
  markGut("volume", gut.saved ? String(gut.carb_multiplier) : "1");
  markGut("climb", gut.saved ? gut.climb_sensitivity : "normal");
  markGut("sweet", gut.saved && gut.early_savoury ? "early" : "ok");
}

export function markGut(group, value) {
  for (const button of document.querySelectorAll(`[data-gut="${group}"]`)) {
    if (button.dataset.value === value) button.setAttribute("aria-checked", "true");
    else button.removeAttribute("aria-checked");
  }
}

export function closeGutModal() {
  document.querySelector("#gut-modal")?.classList.add("hidden");
}

export function saveGut() {
  const volume = document.querySelector('[data-gut="volume"][aria-checked="true"]');
  const climb = document.querySelector('[data-gut="climb"][aria-checked="true"]');
  const sweet = document.querySelector('[data-gut="sweet"][aria-checked="true"]');
  const multiplier = Math.min(1.25, Math.max(0.75, Number(volume?.dataset.value || 1)));
  state.gut = {
    carb_multiplier: multiplier,
    climb_sensitivity: climb?.dataset.value === "sensitive" ? "sensitive" : "normal",
    early_savoury: sweet?.dataset.value === "early",
    saved: true,
  };
  try {
    localStorage.setItem("fuel-gut", JSON.stringify(state.gut));
  } catch {
    /* the pill still shows for this visit */
  }
  renderGutPill();
  closeGutModal();
}

export function resetGut() {
  state.gut = { carb_multiplier: 1, climb_sensitivity: "normal", early_savoury: false, saved: false };
  try {
    localStorage.removeItem("fuel-gut");
  } catch {
    /* ignore */
  }
  renderGutPill();
}
