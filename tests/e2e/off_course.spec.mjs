import path from "node:path";
/** Off-course state: far from the course, Live hides planned grade, zone and arrival until the next good check-in. */

import { expect, test } from "@playwright/test";

const ON_COURSE = { latitude: 42.563584, longitude: 23.278586, accuracy: 8 };
// About 150 km north-east of Vitosha: far past the 2 km "Off course" threshold.
const FAR_AWAY = { latitude: 43.8, longitude: 24.6, accuracy: 8 };

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

test("shows Off course and hides grade, zone and arrival when far from the course", async ({ page }) => {
  test.setTimeout(180000);
  await page.addInitScript((far) => {
    window.__pos = far;
    const geo = {
      getCurrentPosition: (ok) => setTimeout(() => ok({ coords: { ...window.__pos }, timestamp: Date.now() }), 10),
      watchPosition: () => 1,
      clearWatch: () => {},
    };
    Object.defineProperty(navigator, "geolocation", { value: geo, configurable: true });
  }, FAR_AWAY);

  await page.goto("/");
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 120000 });
  await page.locator("#gpx-file").setInputFiles(path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx"));
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-12.gpx", { timeout: 60000 });
  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("packing-list")).not.toBeEmpty({ timeout: 60000 });

  await page.locator("#nav-live").click();
  await page.getByTestId("start-race-cta").click();
  await expect(page.getByTestId("race-countdown-overlay")).toBeHidden({ timeout: 8000 });
  await expect(page.getByTestId("race-cockpit")).toBeVisible();

  // Before any check-in the planned values show.
  await expect(page.getByTestId("eta-block")).toBeVisible();
  await expect(page.getByTestId("terrain-strip")).toBeVisible();

  await page.getByTestId("sync-gps").click();
  await expect(page.locator("#off-course")).toContainText("OFF-COURSE WARNING", { timeout: 30000 });
  await expect(page.getByTestId("current-section-title")).toHaveText("Off course");
  await expect(page.getByTestId("eta-block")).toBeHidden();
  await expect(page.getByTestId("terrain-strip")).toBeHidden();

  // Back on the course: the next check-in restores planned values.
  await page.evaluate(async (pos) => {
    const { getGpsGuard } = await import("/gps.js");
    getGpsGuard().reset();
    window.__pos = pos;
  }, ON_COURSE);
  await page.getByTestId("sync-gps").click();
  await expect(page.getByTestId("eta-block")).toBeVisible({ timeout: 30000 });
  await expect(page.getByTestId("terrain-strip")).toBeVisible();
  await expect(page.getByTestId("current-section-title")).not.toHaveText("Off course");
  await expect(page.locator("#off-course")).toBeHidden();
});
