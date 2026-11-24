import path from "node:path";
import { expect, test } from "@playwright/test";

const GPX = path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx");
const ON_COURSE = {
  latitude: 42.582535995170474,
  longitude: 23.292520008981228,
};

test.use({ locale: "en-GB", serviceWorkers: "allow" });

test("setup, map, checklist, and diagnostics are on the page", async ({ page }) => {
  await page.goto("/");

  await expect(page.getByTestId("gpx-drop")).toBeVisible();
  await expect(page.locator("#weight")).toBeEnabled();
  await expect(page.getByTestId("build-plan")).toBeVisible();

  await page.locator("#pane-ready").click();
  await expect(page.locator("#ready-pane")).toContainText("Check this on Wi-Fi before race day.");

  await page.locator("#nav-map").click();
  await expect(page.locator("#map-empty")).toBeVisible();

  await page.goto("/?diag=1");
  await expect(page.locator("#diag-panel")).toBeVisible();
  await expect(page.locator("#diag-copy")).toHaveText("Copy as JSON");
});

test("builds and recalibrates with no signal", async ({ page, context }) => {
  test.setTimeout(360000);
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation(ON_COURSE);
  await page.goto("/");

  await page.getByTestId("gpx-file").setInputFiles(GPX);
  await expect(page.getByTestId("gpx-name")).toContainText(
    "Loaded salomon-vitosha-12.gpx", { timeout: 90000 }
  );

  await page.locator("#aid-name").fill("Cherni Vrah");
  await page.locator("#aid-km").fill("4");
  await page.locator("#add-aid").click();
  await page.locator("#target-hours").fill("2");
  await page.locator("#target-minutes").fill("00");

  await expect(page.getByTestId("offline-status")).toHaveText(
    "Ready for offline", { timeout: 180000 }
  );
  await page.locator("#pane-ready").click();
  await expect(page.locator("#offline-ready-line")).toContainText(
    "offline ready: yes", { timeout: 30000 }
  );
  await page.waitForFunction(() => Boolean(navigator.serviceWorker.controller));
  await page.locator("#pane-configure").click();

  await context.setOffline(true);
  await expect.poll(() => page.evaluate(() => navigator.onLine)).toBe(false);

  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("packing-list").locator(".pack-card").first())
    .toContainText("Cherni Vrah", { timeout: 120000 });

  await page.locator("#nav-live").click();
  await page.getByTestId("start-race-cta").click();
  await expect(page.getByTestId("race-countdown-overlay")).toBeHidden({ timeout: 10000 });
  await page.getByTestId("live-menu-btn").click();
  await expect(page.getByTestId("race-menu-modal")).toBeVisible();
  await page.getByTestId("gi-rescue").click();

  await expect(page.getByTestId("live-message")).toContainText(
    "GI Rescue is on", { timeout: 120000 }
  );
});
