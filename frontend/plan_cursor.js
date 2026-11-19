/** Where the runner is on the plan: next fuel stop, current segment, next aid. */

import { positionOnCourse } from "./map_controller.js?v=23";
import { segmentAtMinute } from "./offline_engine.js";
import { state } from "./state.js";
import { elapsedMin } from "./ui_helpers.js";

export function nextStop() {
  const plan = state.livePlan;
  if (!plan) {
    return null;
  }
  if (state.sim.active) {
    return plan.fuel_stops.find((stop) => stop.scheduled_minute > state.sim.elapsed + 0.05) || null;
  }
  return plan.fuel_stops.find((stop) => !state.logged.includes(stop.sequence)) || null;
}

export function currentSegment() {
  const segments = state.packedPlan?.segments || [];
  if (state.sim.active) {
    return positionOnCourse(segments, state.sim.km * 1000)?.segment || null;
  }
  if (state.snap) {
    return segments.find((segment) => state.snap.distanceM >= segment.start_distance_m && state.snap.distanceM < segment.end_distance_m)
      || segments[segments.length - 1]
      || null;
  }
  return segmentAtMinute(segments, elapsedMin());
}

export function nextAid(elapsed) {
  const plan = state.livePlan;
  if (!plan?.aid_legs.length) {
    return null;
  }
  let cursor = plan.clock_start_min;
  for (const leg of plan.aid_legs) {
    const end = cursor + leg.predicted_minutes;
    if (end > elapsed + 0.05 && leg.to_name !== "Finish") {
      return { name: leg.to_name, km: leg.to_km, etaMin: Math.max(0, end - elapsed) };
    }
    if (end > elapsed + 0.05) {
      return { name: "Finish", km: leg.to_km, etaMin: Math.max(0, end - elapsed) };
    }
    cursor = end;
  }
  return null;
}

export function shortIntake(stop) {
  if (stop.at_aid_station) {
    if (stop.reason && !stop.reason.startsWith("Aid Station Plate")) return stop.reason;
    return `Aid plate · ${Math.round(stop.carbs_g)}g`;
  }
  if (/^Hydration check/i.test(stop.reason || "")) return stop.reason;
  if (stop.form === "rescue_sip" || (stop.form === "sip" && stop.carbs_g === 0)) return "Water and salt sip";
  const name = stop.product || "";
  if (/tiny bites/i.test(stop.reason || "")) {
    return `Tiny bites of ${name || "food"} (${Math.round(stop.carbs_g)}g)`;
  }
  if (stop.form === "micro_dose" || stop.form === "liquid") {
    const label = name && !/gel/i.test(name) ? name : "Liquid";
    return `${label} ${Math.round(stop.carbs_g)}g · small sips`;
  }
  return `Take 1× ${name || "Fuel"} (${Math.round(stop.carbs_g)}g)`;
}
