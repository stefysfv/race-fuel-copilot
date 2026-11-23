import path from "node:path";
/** Minimal Light check-in guide: Launch -> 3-2-1-GO -> Clock fueling windows -> Section check-in -> Crew Sheet -> Recap. */

import { expect, test } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

test("runs Launch -> 3-2-1-GO -> fueling windows -> section check-in -> Race Menu -> Finish Recap", async ({ page }) => {
  test.setTimeout(180000);
  await page.addInitScript(() => {
    window.__watchCalls = 0;
    const pos = {
      coords: { latitude: 42.563584, longitude: 23.278586, accuracy: 8 },
      timestamp: Date.now(),
    };
    const geo = {
      getCurrentPosition: (ok) => setTimeout(() => ok({ ...pos, timestamp: Date.now() }), 10),
      watchPosition: (ok) => {
        window.__watchCalls += 1;
        setTimeout(() => ok({ ...pos, timestamp: Date.now() }), 10);
        return 1;
      },
      clearWatch: () => {},
    };
    Object.defineProperty(navigator, "geolocation", { value: geo, configurable: true });
  });

  await page.goto("/");
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 120000 });
  await page.locator("#gpx-file").setInputFiles(path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx"));
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-12.gpx", { timeout: 60000 });
  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("packing-list")).not.toBeEmpty({ timeout: 60000 });

  await page.locator("#nav-live").click();
  await expect(page.getByTestId("race-launch")).toBeVisible();
  await expect(page.getByTestId("race-launch-eta")).toContainText("Starting now puts finish around");

  await page.getByTestId("start-race-cta").click();
  await expect(page.getByTestId("race-countdown-overlay")).toBeVisible();
  await expect(page.getByTestId("race-countdown-overlay")).toBeHidden({ timeout: 8000 });
  await expect(page.getByTestId("race-cockpit")).toBeVisible();
  await expect(page.getByTestId("race-live-badge")).toHaveText("LIVE");

  await expect(page.getByTestId("current-eta-clock")).toHaveText(/^~\d{2}:\d{2}$/);
  await expect(page.getByTestId("fuel-window").first()).toBeVisible();

  // The phone stays locked: nothing reads GPS until the runner taps Sync GPS.
  await expect(page.getByTestId("live-progress-line")).not.toContainText("passed");
  await page.getByTestId("sync-gps").click();
  await expect(page.getByTestId("live-progress-line")).toContainText("passed", { timeout: 30000 });
  expect(await page.evaluate(() => window.__watchCalls)).toBe(0);
  await page.getByTestId("live-crew-btn").click();
  await expect(page.getByTestId("crew-live-banner")).toContainText("Live GPS updated");
  await page.locator("#close-crew").click();

  await page.getByTestId("log-intake").click();
  await expect(page.getByTestId("race-log-card")).toBeVisible();
  await page.getByTestId("mark-section-done").click();
  await expect(page.getByTestId("live-completed-details")).toBeVisible();

  await page.getByTestId("live-menu-btn").click();
  await expect(page.getByTestId("race-menu-modal")).toBeVisible();
  await page.getByTestId("open-recap-live").click();
  await expect(page.getByTestId("recap-modal")).toBeVisible();
  await expect(page.getByTestId("recap-summary")).toContainText("1 logged");
  await page.getByTestId("recap-to-feedback").click();
  await expect(page.getByTestId("feedback-notes")).toHaveValue(/Logged intakes:/);

  await page.locator("#nav-live").click();
  await page.getByTestId("live-menu-btn").click();
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("PAUSED");
  const pausedClock = await page.getByTestId("race-elapsed").innerText();
  await page.waitForTimeout(1300);
  await expect(page.getByTestId("race-elapsed")).toHaveText(pausedClock);

  await page.getByTestId("live-menu-btn").click();
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("LIVE");

  await page.getByTestId("live-menu-btn").click();
  await page.getByTestId("race-restart").click();
  await expect(page.getByTestId("restart-modal")).toContainText("Are you sure you want to restart?");
  await page.getByTestId("confirm-restart").click();
  await expect(page.getByTestId("race-launch")).toBeVisible();
});
