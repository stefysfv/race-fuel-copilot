/**
- Prompt 17: Battery & race-day endurance checks.
- Synthetic noisy GPS tracks, adaptive polling, ?diag=1 telemetry,
- Battery Saver mode, wake-lock lifecycle, background resume catch-up,
- and >= 48 px tap targets for gloves & sweat.
 */

import { expect, test } from "@playwright/test";

const ON_COURSE = {
  latitude: 42.582535995170474,
  longitude: 23.292520008981228,
};

test.use({
  viewport: { width: 390, height: 844 },
  locale: "en-GB",
  serviceWorkers: "allow",
});

test("filters synthetic noisy GPS tracks and selects adaptive polling thresholds", async ({ page }) => {
  await page.goto("/");
  const out = await page.evaluate(async () => {
    const { createGpsGuard, pollingPolicy } = await import("/gps_guard.js");
    const guard = createGpsGuard();
    const t0 = 1_700_000_000_000;

    // 1. Clean initial fix accepted.
    const r1 = guard.evaluate({ latitude: 42.5825, longitude: 23.2925, accuracy: 8, timestamp: t0 });
    // 2. Poor accuracy (> 50 m) rejected.
    const r2 = guard.evaluate({ latitude: 42.5826, longitude: 23.2925, accuracy: 95, timestamp: t0 + 10_000 });
    // 3. Normal running step (~35 m in 10 s = 3.5 m/s) accepted.
    const r3 = guard.evaluate({ latitude: 42.5828, longitude: 23.2925, accuracy: 10, timestamp: t0 + 10_000 });
    // 4. Single impossible teleport spike (~2.2 km in 5 s) rejected.
    const r4 = guard.evaluate({ latitude: 42.6028, longitude: 23.2925, accuracy: 9, timestamp: t0 + 15_000 });
    // 5. Return to normal trail position accepted (spike never corrupted state).
    const r5 = guard.evaluate({ latitude: 42.5831, longitude: 23.2925, accuracy: 9, timestamp: t0 + 20_000 });
    // 6. Tunnel / screen-locked jump: first new fix held as suspect, second consistent fix recovers.
    const r6 = guard.evaluate({ latitude: 42.6200, longitude: 23.2925, accuracy: 10, timestamp: t0 + 30_000 });
    const r7 = guard.evaluate({ latitude: 42.6202, longitude: 23.2925, accuracy: 10, timestamp: t0 + 40_000 });

    return {
      r1,
      r2,
      r3,
      r4,
      r5,
      r6,
      r7,
      steadyStd: pollingPolicy({ batterySaver: false, stopEtaMin: 25, aidDistanceM: 4000, offCourseM: 0 }),
      approachStd: pollingPolicy({ batterySaver: false, stopEtaMin: 6, aidDistanceM: 4000, offCourseM: 0 }),
      steadySaver: pollingPolicy({ batterySaver: true, stopEtaMin: 25, aidDistanceM: 4000, offCourseM: 0 }),
      approachSaver: pollingPolicy({ batterySaver: true, stopEtaMin: 25, aidDistanceM: 600, offCourseM: 0 }),
    };
  });

  expect(out.r1.accepted).toBe(true);
  expect(out.r2.accepted).toBe(false);
  expect(out.r2.reason).toBe("accuracy");
  expect(out.r3.accepted).toBe(true);
  expect(out.r4.accepted).toBe(false);
  expect(out.r4.reason).toBe("jump");
  expect(out.r5.accepted).toBe(true);
  expect(out.r6.accepted).toBe(false);
  expect(out.r6.reason).toBe("jump");
  expect(out.r7.accepted).toBe(true);
  expect(out.r7.reason).toBe("recovered");

  expect(out.steadyStd.state).toBe("steady");
  expect(out.steadyStd.mode).toBe("watch");
  expect(out.steadyStd.recalIntervalMs).toBeGreaterThan(out.approachStd.recalIntervalMs);
  expect(out.approachStd.state).toBe("approach");
  expect(out.steadySaver.mode).toBe("poll");
  expect(out.steadySaver.recalIntervalMs).toBeGreaterThan(out.steadyStd.recalIntervalMs);
  expect(out.approachSaver.state).toBe("approach");
  expect(out.approachSaver.pollIntervalMs).toBeLessThan(out.steadySaver.pollIntervalMs);
});

test("supports diag telemetry, Battery Saver, wake lock, catch-up resume, bad-fix rejection, and 48px targets", async ({ page, context }) => {
  test.setTimeout(240000);
  await page.addInitScript(() => {
    window.__wakeRequested = 0;
    window.__wakeReleased = 0;
    window.__recalCalls = 0;
    const origPost = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (message, ...args) {
      if (message && message.type === "recalibrate") {
        window.__recalCalls += 1;
      }
      return origPost.call(this, message, ...args);
    };
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: {
        async request() {
          window.__wakeRequested += 1;
          const listeners = [];
          return {
            released: false,
            addEventListener(type, fn) {
              if (type === "release") listeners.push(fn);
            },
            async release() {
              window.__wakeReleased += 1;
              this.released = true;
              listeners.forEach((fn) => fn());
            },
          };
        },
      },
    });
  });

  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ ...ON_COURSE, accuracy: 8 });

  await page.goto("/?diag=1");
  await expect(page.locator("#diag-list")).toContainText("GPS fix interval");
  await expect(page.locator("#diag-list")).toContainText("Recalibrations");
  await expect(page.locator("#diag-list")).toContainText("Speech / notifications");
  await expect(page.locator("#diag-list")).toContainText("Wake lock");
  await expect(page.locator("#diag-list")).toContainText("Work per hour");

  await page.goto("/");
  await expect(page.locator("#race-select option[value='salomon-vitosha-12']")).toHaveCount(1, { timeout: 30000 });
  await page.getByTestId("race-select").selectOption("salomon-vitosha-12");
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 180000 });
  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("summary")).toContainText("Salomon Vitosha Trail 12 km", { timeout: 180000 });

  await page.locator("#nav-live").click();
  await expect(page.getByTestId("race-launch")).toBeVisible();
  await expect(page.getByTestId("race-cockpit")).toBeHidden();

  // Toggle Battery Saver from the launch card and verify OLED dark mode class persists.
  await page.getByTestId("launch-battery-saver").click();
  await expect(page.locator("body")).toHaveClass(/battery-saver/);
  await expect(page.getByTestId("launch-battery-saver")).toHaveAttribute("aria-pressed", "true");

  // Start Race -> acquires wake lock and opens cockpit.
  await page.getByTestId("start-race-cta").click();
  await page.getByTestId("race-countdown-skip").click();
  await expect(page.getByTestId("race-cockpit")).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__wakeRequested)).toBeGreaterThanOrEqual(1);
  await expect(page.locator("body")).toHaveClass(/race-active/);

  // Check glove-friendly labels and >= 48px minimum tap target heights on active cockpit.
  await expect(page.getByTestId("log-intake")).toHaveText("I ate");
  await expect(page.getByTestId("gi-rescue")).toHaveText("GI trouble");
  for (const tid of ["log-intake", "gi-rescue", "sync-gps", "race-pause", "race-restart", "battery-saver", "live-crew-btn"]) {
    const box = await page.getByTestId(tid).boundingBox();
    expect(box, `missing box for ${tid}`).not.toBeNull();
    expect(box.height, `${tid} height < 48px`).toBeGreaterThanOrEqual(48);
    expect(box.width, `${tid} width < 48px`).toBeGreaterThanOrEqual(48);
  }
  await expect(page.getByTestId("battery-saver")).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#gps-banner")).toContainText("GPS Locked", { timeout: 30000 });
  await expect.poll(() => page.evaluate(() => window.__recalCalls), { timeout: 30000 }).toBeGreaterThanOrEqual(1);

  // Inject a weak-accuracy fix and an impossible jump fix: neither may trigger recalibration.
  const beforeRecals = await page.evaluate(() => window.__recalCalls);
  await page.evaluate(async () => {
    const { onGpsSuccess } = await import("/gps.js");
    onGpsSuccess({ coords: { latitude: 42.58253, longitude: 23.29252, accuracy: 140 }, timestamp: Date.now() });
    onGpsSuccess({ coords: { latitude: 43.9, longitude: 24.9, accuracy: 8 }, timestamp: Date.now() + 2000 });
  });
  await expect(page.locator("#gps-banner")).toContainText(/Weak GPS|GPS jump ignored/);
  const afterBadRecals = await page.evaluate(() => window.__recalCalls);
  expect(afterBadRecals).toBe(beforeRecals);

  // Simulate backgrounding for > 10s then returning -> calm catching-up state appears.
  const sawCatchUp = await page.evaluate(async () => {
    const { state } = await import("/state.js");
    const { handleVisibilityChange } = await import("/gps.js");
    state.hiddenAt = Date.now() - 25_000;
    handleVisibilityChange();
    const banner = document.querySelector("#catching-up");
    return {
      text: banner?.textContent || "",
      visible: Boolean(banner && !banner.classList.contains("hidden")),
    };
  });
  expect(sawCatchUp.visible).toBe(true);
  expect(sawCatchUp.text).toContain("Catching up");

  // Pause Race Mode -> wake lock released.
  await page.getByTestId("race-pause").click();
  await expect.poll(() => page.evaluate(() => window.__wakeReleased)).toBeGreaterThanOrEqual(1);
});
