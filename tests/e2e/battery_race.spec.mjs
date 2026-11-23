import path from "node:path";
/** GPS guard, check-in diagnostics, no background GPS or wake lock, unlock resume, and >= 48px Live tap targets. */

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

test("filters synthetic noisy GPS tracks", async ({ page }) => {
  await page.goto("/");
  const out = await page.evaluate(async () => {
    const { createGpsGuard } = await import("/gps_guard.js");
    const guard = createGpsGuard();
    const t0 = 1_700_000_000_000;

    const r1 = guard.evaluate({ latitude: 42.5825, longitude: 23.2925, accuracy: 8, timestamp: t0 });
    const r2 = guard.evaluate({ latitude: 42.5826, longitude: 23.2925, accuracy: 95, timestamp: t0 + 10_000 });
    const r3 = guard.evaluate({ latitude: 42.5828, longitude: 23.2925, accuracy: 10, timestamp: t0 + 10_000 });
    const r4 = guard.evaluate({ latitude: 42.6028, longitude: 23.2925, accuracy: 9, timestamp: t0 + 15_000 });
    const r5 = guard.evaluate({ latitude: 42.5831, longitude: 23.2925, accuracy: 9, timestamp: t0 + 20_000 });
    const r6 = guard.evaluate({ latitude: 42.6200, longitude: 23.2925, accuracy: 10, timestamp: t0 + 30_000 });
    const r7 = guard.evaluate({ latitude: 42.6202, longitude: 23.2925, accuracy: 10, timestamp: t0 + 40_000 });

    return {
      r1, r2, r3, r4, r5, r6, r7,
      hasPollingPolicy: "pollingPolicy" in (await import("/gps_guard.js")),
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

  // Check-in concept: nothing polls, so there is no adaptive polling policy any more.
  expect(out.hasPollingPolicy).toBe(false);
});

test("checks in only on Sync GPS: no watch, no wake lock, no GPS on unlock, bad fixes rejected, 48px targets", async ({ page, context }) => {
  test.setTimeout(240000);
  await page.addInitScript(() => {
    window.__wakeRequested = 0;
    window.__geoCalls = { watch: 0, current: 0 };
    window.__recalCalls = 0;
    const origPost = Worker.prototype.postMessage;
    Worker.prototype.postMessage = function (message, ...args) {
      if (message && message.type === "recalibrate") {
        window.__recalCalls += 1;
      }
      return origPost.call(this, message, ...args);
    };
    // The app must never ask for a wake lock or watch GPS. Count any attempt.
    Object.defineProperty(navigator, "wakeLock", {
      configurable: true,
      value: {
        async request() {
          window.__wakeRequested += 1;
          return { released: false, addEventListener() {}, async release() {} };
        },
      },
    });
    const geo = navigator.geolocation;
    const realWatch = geo.watchPosition.bind(geo);
    const realCurrent = geo.getCurrentPosition.bind(geo);
    geo.watchPosition = (...args) => {
      window.__geoCalls.watch += 1;
      return realWatch(...args);
    };
    geo.getCurrentPosition = (...args) => {
      window.__geoCalls.current += 1;
      return realCurrent(...args);
    };
  });

  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ ...ON_COURSE, accuracy: 8 });

  await page.goto("/?diag=1");
  await expect(page.locator("#diag-list")).toContainText("Check-in interval");
  await expect(page.locator("#diag-list")).toContainText("Recalibrations");
  await expect(page.locator("#diag-list")).toContainText("Battery Saver");
  await expect(page.locator("#diag-list")).not.toContainText("Wake lock");
  await expect(page.locator("#diag-list")).not.toContainText("Polling");

  await page.goto("/");
  await page.locator("#gpx-file").setInputFiles(path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx"));
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-12.gpx", { timeout: 60000 });
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 180000 });
  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("summary")).not.toBeEmpty({ timeout: 180000 });

  await page.locator("#nav-live").click();
  await expect(page.getByTestId("race-launch")).toBeVisible();
  await expect(page.getByTestId("race-cockpit")).toBeHidden();

  await page.getByTestId("start-race-cta").click();
  await page.getByTestId("race-countdown-skip").click();
  await expect(page.getByTestId("race-cockpit")).toBeVisible();

  // Starting the race reads no GPS and holds no wake lock.
  await page.waitForTimeout(1500);
  expect(await page.evaluate(() => window.__geoCalls)).toEqual({ watch: 0, current: 0 });
  expect(await page.evaluate(() => window.__wakeRequested)).toBe(0);

  for (const tid of ["log-intake", "sync-gps", "live-crew-btn", "live-menu-btn"]) {
    const box = await page.getByTestId(tid).boundingBox();
    expect(box, `missing box for ${tid}`).not.toBeNull();
    expect(box.height, `${tid} height < 48px`).toBeGreaterThanOrEqual(48);
    expect(box.width, `${tid} width < 48px`).toBeGreaterThanOrEqual(48);
  }
  await page.getByTestId("sync-gps").click();
  await expect(page.locator("#gps-banner")).toContainText("GPS Locked", { timeout: 30000 });
  await expect.poll(() => page.evaluate(() => window.__recalCalls), { timeout: 30000 }).toBeGreaterThanOrEqual(1);
  expect(await page.evaluate(() => window.__geoCalls)).toEqual({ watch: 0, current: 1 });

  const beforeRecals = await page.evaluate(() => window.__recalCalls);
  await page.evaluate(async () => {
    const { onGpsSuccess } = await import("/gps.js");
    onGpsSuccess({ coords: { latitude: 42.58253, longitude: 23.29252, accuracy: 140 }, timestamp: Date.now() });
    onGpsSuccess({ coords: { latitude: 43.9, longitude: 24.9, accuracy: 8 }, timestamp: Date.now() + 2000 });
  });
  await expect(page.locator("#gps-banner")).toContainText(/Weak GPS|GPS jump ignored/);
  const afterBadRecals = await page.evaluate(() => window.__recalCalls);
  expect(afterBadRecals).toBe(beforeRecals);

  // Unlocking the phone refreshes the clock screen and never reads GPS.
  const geoBeforeUnlock = await page.evaluate(() => ({ ...window.__geoCalls }));
  await page.evaluate(async () => {
    const { state } = await import("/state.js");
    const { handleVisibilityChange } = await import("/gps.js");
    state.hiddenAt = Date.now() - 25_000;
    handleVisibilityChange();
  });
  expect(await page.evaluate(() => window.__geoCalls)).toEqual(geoBeforeUnlock);
  expect(await page.evaluate(() => window.__recalCalls)).toBe(afterBadRecals);

  await page.getByTestId("live-menu-btn").click();
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("PAUSED");
  expect(await page.evaluate(() => window.__wakeRequested)).toBe(0);
  expect((await page.evaluate(() => window.__geoCalls)).watch).toBe(0);
});
