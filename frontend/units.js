/** Single display-unit module (metric / imperial). The engine stays metric. */

import { readText, UNITS_KEY, writeText } from "./local_store.js";

export const KM_PER_MILE = 1.609344;
export const METERS_PER_FOOT = 0.3048;
export const KG_PER_POUND = 0.45359237;

const IMPERIAL_REGIONS = new Set(["US", "LR", "MM"]);

let currentSystem = null;
const listeners = new Set();

export function detectUnitSystem(locales) {
  const list = Array.isArray(locales) && locales.length
    ? locales
    : (typeof navigator !== "undefined"
      ? (navigator.languages?.length ? [...navigator.languages] : [navigator.language])
      : []);
  const primary = String(list[0] || "").trim();
  const match = primary.match(/[-_]([A-Za-z]{2})(?:$|[-_])/);
  if (match && IMPERIAL_REGIONS.has(match[1].toUpperCase())) {
    return "imperial";
  }
  return "metric";
}

export function getUnitSystem() {
  if (currentSystem === "metric" || currentSystem === "imperial") {
    return currentSystem;
  }
  const saved = readText(UNITS_KEY);
  if (saved === "metric" || saved === "imperial") {
    currentSystem = saved;
  } else {
    currentSystem = detectUnitSystem();
  }
  return currentSystem;
}

export function isImperial(system = getUnitSystem()) {
  return system === "imperial";
}

export function setUnitSystem(system, { persist = true, notify = true } = {}) {
  const next = system === "imperial" ? "imperial" : "metric";
  currentSystem = next;
  if (persist) {
    writeText(UNITS_KEY, next);
  }
  if (notify) {
    for (const fn of listeners) {
      try {
        fn(next);
      } catch (error) {
        console.error(error);
      }
    }
  }
  return next;
}

export function onUnitChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export function kmToMiles(km) {
  return Number(km) / KM_PER_MILE;
}

export function milesToKm(mi) {
  return Number(mi) * KM_PER_MILE;
}

export function metersToFeet(m) {
  return Number(m) / METERS_PER_FOOT;
}

export function feetToMeters(ft) {
  return Number(ft) * METERS_PER_FOOT;
}

export function celsiusToFahrenheit(c) {
  return (Number(c) * 9) / 5 + 32;
}

export function fahrenheitToCelsius(f) {
  return ((Number(f) - 32) * 5) / 9;
}

export function kgToPounds(kg) {
  return Number(kg) / KG_PER_POUND;
}

export function poundsToKg(lb) {
  return Number(lb) * KG_PER_POUND;
}

export function minPerKmToMinPerMile(minPerKm) {
  return Number(minPerKm) * KM_PER_MILE;
}

export function minPerMileToMinPerKm(minPerMile) {
  return Number(minPerMile) / KM_PER_MILE;
}

export function distanceToDisplay(km, system = getUnitSystem()) {
  return isImperial(system) ? kmToMiles(km) : Number(km);
}

export function distanceFromDisplay(value, system = getUnitSystem()) {
  return isImperial(system) ? milesToKm(value) : Number(value);
}

export function elevationToDisplay(meters, system = getUnitSystem()) {
  return isImperial(system) ? metersToFeet(meters) : Number(meters);
}

export function elevationFromDisplay(value, system = getUnitSystem()) {
  return isImperial(system) ? feetToMeters(value) : Number(value);
}

export function tempToDisplay(celsius, system = getUnitSystem()) {
  return isImperial(system) ? celsiusToFahrenheit(celsius) : Number(celsius);
}

export function tempFromDisplay(value, system = getUnitSystem()) {
  return isImperial(system) ? fahrenheitToCelsius(value) : Number(value);
}

export function weightToDisplay(kg, system = getUnitSystem()) {
  return isImperial(system) ? kgToPounds(kg) : Number(kg);
}

export function weightFromDisplay(value, system = getUnitSystem()) {
  return isImperial(system) ? poundsToKg(value) : Number(value);
}

export function paceToDisplay(minPerKm, system = getUnitSystem()) {
  return isImperial(system) ? minPerKmToMinPerMile(minPerKm) : Number(minPerKm);
}

export function paceFromDisplay(value, system = getUnitSystem()) {
  return isImperial(system) ? minPerMileToMinPerKm(value) : Number(value);
}

export function distanceUnit(system = getUnitSystem()) {
  return isImperial(system) ? "mi" : "km";
}

export function elevationUnit(system = getUnitSystem()) {
  return isImperial(system) ? "ft" : "m";
}

export function tempUnit(system = getUnitSystem()) {
  return isImperial(system) ? "°F" : "°C";
}

export function weightUnit(system = getUnitSystem()) {
  return isImperial(system) ? "lb" : "kg";
}

export function paceUnit(system = getUnitSystem()) {
  return isImperial(system) ? "min/mi" : "min/km";
}

export function formatDistance(km, { digits = 1, compact = false, system = getUnitSystem() } = {}) {
  const val = distanceToDisplay(km, system);
  const unit = distanceUnit(system);
  return compact ? `${val.toFixed(digits)}${unit}` : `${val.toFixed(digits)} ${unit}`;
}

export function formatDistanceRounded(km, { compact = true, system = getUnitSystem() } = {}) {
  const val = Math.round(distanceToDisplay(km, system));
  const unit = distanceUnit(system);
  return compact ? `${val}${unit}` : `${val} ${unit}`;
}

export function formatElevation(meters, { signed = false, compact = false, system = getUnitSystem() } = {}) {
  const val = Math.round(elevationToDisplay(meters, system));
  const prefix = signed && val >= 0 ? "+" : "";
  const unit = elevationUnit(system);
  return compact ? `${prefix}${val}${unit}` : `${prefix}${val} ${unit}`;
}

export function formatTemp(celsius, { digits = 0, system = getUnitSystem() } = {}) {
  const val = tempToDisplay(celsius, system);
  return `${val.toFixed(digits)}${tempUnit(system)}`;
}

export function formatWeight(kg, { digits = 0, compact = true, system = getUnitSystem() } = {}) {
  const val = weightToDisplay(kg, system);
  const unit = weightUnit(system);
  return compact ? `${val.toFixed(digits)}${unit}` : `${val.toFixed(digits)} ${unit}`;
}

export function formatPace(minPerKm, { digits = 2, system = getUnitSystem() } = {}) {
  const val = paceToDisplay(minPerKm, system);
  return `${val.toFixed(digits)} ${paceUnit(system)}`;
}

export function formatShortLength(meters, system = getUnitSystem()) {
  if (isImperial(system)) {
    return `${Math.round(metersToFeet(meters))} ft`;
  }
  return `${Math.round(meters)} m`;
}

export function formatWeightInputValue(kg, system = getUnitSystem()) {
  const val = weightToDisplay(kg, system);
  return String(Math.round(val * 10) / 10);
}

export function formatTempInputValue(celsius, system = getUnitSystem()) {
  const val = tempToDisplay(celsius, system);
  return String(Math.round(val * 10) / 10);
}
