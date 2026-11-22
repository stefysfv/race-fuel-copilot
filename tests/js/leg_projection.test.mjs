/** Unit tests for the Live check-in projection. Run with: npm run test:js */

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  buildConsumptionWindows,
  computePaceContext,
  formatTimeOfDay,
  isFarOffCourse,
  legKey,
  OFF_COURSE_HIDE_M,
  PACE_RATIO_MAX,
  PACE_RATIO_MIN,
  projectLegs,
} from "../../frontend/leg_projection.js";

const MIN = 60_000;
const NOW = Date.UTC(2026, 5, 1, 8, 0, 0);

/** Three sections: 60 + 90 + 60 planned minutes, 30 km. */
function makePlan(overrides = {}) {
  const legs = [
    { from_name: "Start", to_name: "Aid 1", from_km: 0, to_km: 10, predicted_minutes: 60, station_meal: "Aid Station Plate", station_carbs_g: 40, pack: [] },
    { from_name: "Aid 1", to_name: "Aid 2", from_km: 10, to_km: 20, predicted_minutes: 90, station_meal: "", station_carbs_g: 0, pack: [] },
    { from_name: "Aid 2", to_name: "Finish", from_km: 20, to_km: 30, predicted_minutes: 60, station_meal: "", station_carbs_g: 0, pack: [] },
  ];
  const gel = (sequence, distance_km, scheduled_minute, extra = {}) => ({
    sequence, distance_km, scheduled_minute, product: "Standard Gel", form: "gel", carbs_g: 25, at_aid_station: false, ...extra,
  });
  return {
    clock_start_min: 0,
    aid_legs: legs,
    fuel_stops: [gel(1, 2, 15), gel(2, 4, 30), gel(3, 12, 100), gel(4, 15, 130), gel(5, 10, 60, { at_aid_station: true })],
    ...overrides,
  };
}

function makeRun(overrides = {}) {
  return {
    sectionLogs: [],
    lastGpsRecalAt: 0,
    snap: null,
    completedLegs: [],
    passedDistanceM: 0,
    packedPlan: makePlan(),
    logged: [],
    pausedAt: null,
    now: NOW,
    ...overrides,
  };
}

function logFor(plan, legIndex, plannedCumMin, actualElapsedMin) {
  const leg = plan.aid_legs[legIndex];
  return { key: legKey(leg), toKm: leg.to_km, plannedCumMin, actualElapsedMin, clockMs: NOW };
}

describe("computePaceContext", () => {
  test("uses the planned pace when there is no log", () => {
    const pace = computePaceContext(makePlan(), 10, makeRun());
    assert.equal(pace.ratio, 1);
    assert.equal(pace.basis, "planned pace");
    assert.equal(pace.anchorAtLegEnd, null);
    assert.equal(pace.anchorElapsedMin, 10);
  });

  test("on-pace log keeps ratio 1 and says so", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 60, 61)] });
    const pace = computePaceContext(plan, 61, run);
    assert.ok(Math.abs(pace.ratio - 61 / 60) < 1e-9);
    assert.equal(pace.basis, "on pace from your log");
    assert.equal(pace.anchorAtLegEnd, legKey(plan.aid_legs[0]));
    assert.equal(pace.anchorElapsedMin, 61);
  });

  test("a log 20% slower than plan scales the ratio to 1.2", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 60, 72)] });
    const pace = computePaceContext(plan, 72, run);
    assert.ok(Math.abs(pace.ratio - 1.2) < 1e-9);
    assert.equal(pace.basis, "+20% pace from your log");
  });

  test("a log faster than plan reports a negative percentage", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 60, 54)] });
    const pace = computePaceContext(plan, 54, run);
    assert.equal(pace.basis, "-10% pace from your log");
  });

  test("ratio is clamped at the lower bound", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 60, 30)] });
    const pace = computePaceContext(plan, 30, run);
    assert.equal(PACE_RATIO_MIN, 0.7);
    assert.equal(pace.ratio, 0.7);
    assert.equal(pace.basis, "-30% pace from your log");
  });

  test("ratio is clamped at the upper bound", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 60, 150)] });
    const pace = computePaceContext(plan, 150, run);
    assert.equal(PACE_RATIO_MAX, 1.85);
    assert.equal(pace.ratio, 1.85);
    assert.equal(pace.basis, "+85% pace from your log");
  });

  test("a tiny first section is too short to learn pace from", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 2, 5)] });
    assert.equal(computePaceContext(plan, 5, run).basis, "planned pace");
  });

  test("a GPS check-in newer than the last log wins and resets the ratio", () => {
    const plan = makePlan();
    const run = makeRun({
      sectionLogs: [logFor(plan, 0, 60, 72)],
      lastGpsRecalAt: NOW + 5 * MIN,
      snap: { distanceM: 12_000 },
    });
    const pace = computePaceContext(plan, 80, run);
    assert.equal(pace.ratio, 1);
    assert.equal(pace.basis, "based on GPS check-in");
  });

  test("does not throw without a plan", () => {
    assert.equal(computePaceContext(null, 0, makeRun()).startClockMin, 0);
  });
});

describe("projectLegs", () => {
  test("on pace: every section is ahead and arrival times add up", () => {
    const plan = makePlan();
    const { ahead, done, pace } = projectLegs(plan, 0, makeRun());
    assert.equal(pace.ratio, 1);
    assert.equal(done.length, 0);
    assert.deepEqual(ahead.map((a) => a.etaElapsedMin), [60, 150, 210]);
    assert.equal(ahead[0].etaClockMs, NOW + 60 * MIN);
    assert.equal(ahead[0].remMin, 60);
  });

  test("+20% slower log pushes every later arrival out", () => {
    const plan = makePlan();
    const run = makeRun({
      sectionLogs: [logFor(plan, 0, 60, 72)],
      completedLegs: [legKey(plan.aid_legs[0])],
      passedDistanceM: 10_000,
    });
    const { ahead, done, pace } = projectLegs(plan, 72, run);
    assert.ok(Math.abs(pace.ratio - 1.2) < 1e-9);
    assert.equal(done.length, 1);
    assert.equal(ahead.length, 2);
    // Anchor is the logged 72 min. Section 2: 90 * 1.2 = 108. Section 3: 60 * 1.2 = 72.
    assert.ok(Math.abs(ahead[0].etaElapsedMin - 180) < 1e-9);
    assert.ok(Math.abs(ahead[1].etaElapsedMin - 252) < 1e-9);
    assert.ok(Math.abs(ahead[0].remMin - 108) < 1e-9);
    assert.ok(Math.abs(ahead[0].etaClockMs - (NOW + 108 * MIN)) < 1);
  });

  test("clamped ratio is what the arrival times use", () => {
    const plan = makePlan();
    const run = makeRun({
      sectionLogs: [logFor(plan, 0, 60, 150)],
      completedLegs: [legKey(plan.aid_legs[0])],
    });
    const { ahead } = projectLegs(plan, 150, run);
    assert.ok(Math.abs(ahead[0].scaledLegMin - 90 * 1.85) < 1e-9);
  });

  test("a completed section leaves ahead and moves to done", () => {
    const plan = makePlan();
    const run = makeRun({ completedLegs: [legKey(plan.aid_legs[0])] });
    const { ahead, done } = projectLegs(plan, 0, run);
    assert.deepEqual(done.map((d) => d.leg.to_name), ["Aid 1"]);
    assert.deepEqual(ahead.map((a) => a.leg.to_name), ["Aid 2", "Finish"]);
  });

  test("restoring a completed section puts it back first in ahead", () => {
    const plan = makePlan();
    const key = legKey(plan.aid_legs[0]);
    const completed = makeRun({ completedLegs: [key], sectionLogs: [logFor(plan, 0, 60, 72)] });
    assert.equal(projectLegs(plan, 72, completed).ahead[0].leg.to_name, "Aid 2");

    // Restore clears both the completed key and its log (see reopenLegCompleted in live.js).
    const restored = makeRun({ completedLegs: [], sectionLogs: [] });
    const { ahead, done, pace } = projectLegs(plan, 72, restored);
    assert.equal(done.length, 0);
    assert.equal(ahead[0].leg.to_name, "Aid 1");
    assert.equal(pace.basis, "planned pace");
  });

  test("distance passed marks earlier sections done even without a tap", () => {
    const plan = makePlan();
    const { ahead, done } = projectLegs(plan, 0, makeRun({ passedDistanceM: 21_000 }));
    assert.deepEqual(done.map((d) => d.leg.to_name), ["Aid 1", "Aid 2"]);
    assert.deepEqual(ahead.map((a) => a.leg.to_name), ["Finish"]);
  });

  test("sections dropped from the live plan after a GPS recalculation stay in the archive", () => {
    const plan = makePlan();
    const livePlan = { ...plan, aid_legs: plan.aid_legs.slice(1) };
    const run = makeRun({ packedPlan: plan, completedLegs: [legKey(plan.aid_legs[0])], passedDistanceM: 10_500 });
    const { done } = projectLegs(livePlan, 70, run);
    const archived = done.find((d) => d.archivedByGps);
    assert.equal(archived.leg.to_name, "Aid 1");
  });

  // Pins behaviour of the branch that used to be an identical-branch ternary.
  test("a log whose section is not marked passed still projects from the clock", () => {
    const plan = makePlan();
    const run = makeRun({ sectionLogs: [logFor(plan, 0, 60, 72)] });
    const { ahead } = projectLegs(plan, 80, run);
    assert.equal(ahead[0].leg.to_name, "Aid 1");
    assert.ok(Math.abs(ahead[0].etaElapsedMin - (80 + 60 * 1.2)) < 1e-9);
  });

  test("a paused race projects from the pause time, not from now", () => {
    const plan = makePlan();
    const pausedAt = NOW - 30 * MIN;
    const { ahead } = projectLegs(plan, 0, makeRun({ pausedAt }));
    assert.equal(ahead[0].etaClockMs, pausedAt + 60 * MIN);
  });

  test("no aid stations: nothing ahead, nothing done, planned pace", () => {
    for (const plan of [makePlan({ aid_legs: [] }), { clock_start_min: 0 }, null]) {
      const result = projectLegs(plan, 12, makeRun({ packedPlan: plan }));
      assert.deepEqual(result.ahead, []);
      assert.deepEqual(result.done, []);
      assert.equal(result.pace.basis, "planned pace");
    }
  });
});

describe("buildConsumptionWindows", () => {
  function firstEntry(plan, run, elapsed = 0) {
    return projectLegs(plan, elapsed, run).ahead[0];
  }

  test("groups the section's trail stops and adds the aid station meal", () => {
    const plan = makePlan();
    const run = makeRun();
    const entry = firstEntry(plan, run);
    const rows = buildConsumptionWindows(plan, entry, 0, projectLegs(plan, 0, run).pace, run);
    assert.equal(rows.length, 2);
    assert.equal(rows[0].kind, "trail");
    assert.equal(rows[0].text, "2× Standard Gel (50g)");
    assert.equal(rows[1].kind, "station");
    assert.match(rows[1].text, /^Potatoes \/ banana \+ cola \(40g\)$/);
    assert.ok(rows[1].time.startsWith("At Aid 1 (~"));
  });

  test("stops already logged are left out", () => {
    const plan = makePlan();
    const run = makeRun({ logged: [1] });
    const entry = firstEntry(plan, run);
    const rows = buildConsumptionWindows(plan, entry, 0, projectLegs(plan, 0, run).pace, run);
    assert.equal(rows[0].text, "1× Standard Gel (25g)");
  });

  test("stops at the aid station are not trail stops", () => {
    const plan = makePlan();
    plan.fuel_stops = plan.fuel_stops.filter((stop) => stop.at_aid_station);
    plan.aid_legs[0].station_meal = "";
    const run = makeRun();
    const entry = firstEntry(plan, run);
    const rows = buildConsumptionWindows(plan, entry, 0, projectLegs(plan, 0, run).pace, run);
    assert.deepEqual(rows.map((r) => r.text), ["Sip water & electrolytes to thirst"]);
  });

  test("a slower pace moves the windows later", () => {
    const plan = makePlan();
    const base = makeRun({ completedLegs: [legKey(plan.aid_legs[0])], passedDistanceM: 10_000 });
    const onPace = projectLegs(plan, 60, base);
    const slowRun = {
      ...base,
      sectionLogs: [logFor(plan, 0, 60, 72)],
    };
    const slow = projectLegs(plan, 72, slowRun);
    const a = buildConsumptionWindows(plan, onPace.ahead[0], 60, onPace.pace, base);
    const b = buildConsumptionWindows(plan, slow.ahead[0], 72, slow.pace, slowRun);
    assert.equal(a[0].text, b[0].text);
    assert.notEqual(a[0].time, b[0].time);
  });

  test("no current section means no windows", () => {
    const plan = makePlan({ aid_legs: [] });
    assert.deepEqual(buildConsumptionWindows(plan, null, 0, computePaceContext(plan, 0, makeRun()), makeRun()), []);
    assert.deepEqual(buildConsumptionWindows({ fuel_stops: [] }, {}, 0, {}, makeRun()), []);
  });
});

describe("off-course rule", () => {
  test("hides planned values only past 2 km", () => {
    assert.equal(OFF_COURSE_HIDE_M, 2000);
    assert.equal(isFarOffCourse(0), false);
    assert.equal(isFarOffCourse(250), false);
    assert.equal(isFarOffCourse(2000), false);
    assert.equal(isFarOffCourse(2001), true);
    assert.equal(isFarOffCourse(101_000), true);
    assert.equal(isFarOffCourse(Number.NaN), false);
    assert.equal(isFarOffCourse(undefined), false);
  });
});

describe("formatTimeOfDay", () => {
  test("rounds to the requested step", () => {
    const d = new Date(2026, 5, 1, 12, 7, 0).getTime();
    assert.equal(formatTimeOfDay(d, 5), "12:05");
    assert.equal(formatTimeOfDay(d, 1), "12:07");
  });
});
