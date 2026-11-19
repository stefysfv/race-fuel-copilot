/**
 * Dev-only smoke pass for the static app. Not part of the race-day site:
 * these files live outside `frontend/` and are not in the service worker
 * precache. Screenshots under tests/e2e/screenshots/ are for a manual
 * side-by-side look. They are not pixel assertions.
 *
 *   npx playwright test tests/e2e
 */

import path from "node:path";

import { expect, test } from "@playwright/test";

const PLAN_MS = 180000;
const SCREENSHOTS = path.resolve("tests/e2e/screenshots");
const GPX = path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx");

// First track point of the bundled 12 km course, so the mocked fix is on the line.
const ON_COURSE = {
  latitude: 42.582535995170474,
  longitude: 23.292520008981228,
};

test.use({
  viewport: { width: 390, height: 844 },
  deviceScaleFactor: 1,
  serviceWorkers: "allow",
});

async function saveView(page, name) {
  await page.screenshot({
    path: path.join(SCREENSHOTS, `${name}.png`),
    animations: "disabled",
  });
}

/** Elevation samples and fuel-stop markers drawn on the chart canvas. */
async function chartCounts(page) {
  return page.evaluate(() => {
    const canvas = document.querySelector("[data-testid='elevation-chart']");
    const chart = window.Chart?.getChart?.(canvas);
    const datasets = chart?.data?.datasets || [];
    const elevation = datasets.find((dataset) => dataset.label === "Elevation (m)");
    const stops = datasets.find((dataset) => dataset.label === "Fuel stop");
    return {
      elevation: elevation?.data?.length || 0,
      stops: stops?.data?.length || 0,
    };
  });
}

test("loads a race, plans online and offline, recalibrates, and accepts a GPX", async ({ page, context }) => {
  test.setTimeout(720000);
  page.on("pageerror", (error) => {
    console.log(`PAGEERROR ${error.message}`);
  });
  await page.addInitScript(() => {
    const original = Worker.prototype.postMessage;
    window.__planCalls = 0;
    Worker.prototype.postMessage = function (message, ...args) {
      if (message && message.type === "generate_plan") {
        window.__planCalls += 1;
      }
      return original.call(this, message, ...args);
    };
  });

  await test.step("load the app and pick the bundled 12 km race", async () => {
    await page.goto("/");
    await expect(page.getByTestId("race-select")).toBeVisible();
    await expect(page.locator("#race-select option[value='salomon-vitosha-12']")).toHaveCount(1);
    await page.getByTestId("race-select").selectOption("salomon-vitosha-12");
    await expect(page.getByTestId("race-select")).toHaveValue("salomon-vitosha-12");
    await expect(page.locator("#target-hours")).toHaveValue("2");
    await expect(page.locator("#target-minutes")).toHaveValue("00");
    await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: PLAN_MS });
    await saveView(page, "setup");
  });

  await test.step("generate a plan and check the main sections", async () => {
    await page.getByTestId("build-plan").click();
    const summary = page.getByTestId("summary");
    await expect(summary).toBeVisible({ timeout: PLAN_MS });
    await expect(summary).toContainText("Salomon Vitosha Trail 12 km");
    await expect(summary).toContainText("/60 g/h");
    await expect.poll(() => page.evaluate(() => window.__planCalls)).toBe(1);
    const card = page.getByTestId("packing-list").locator(".pack-card").first();
    await expect(card).toBeVisible();
    await expect(card).toContainText("Cherni Vrah");
    await expect(page.locator("#toast")).toHaveClass(/hidden/, { timeout: 8000 });
    await saveView(page, "packing");

    await page.locator("#nav-map").click();
    await expect(page.getByTestId("elevation-chart")).toBeVisible();
    await expect.poll(async () => {
      const counts = await chartCounts(page);
      return counts.elevation > 1 && counts.stops > 0;
    }, { timeout: 15000 }).toBe(true);
    const counts = await chartCounts(page);
    expect(counts.stops).toBeGreaterThan(0);
    await expect(page.locator("#map .leaflet-interactive").first()).toBeVisible();
    await page.waitForTimeout(1200);
    await saveView(page, "map");

    await page.locator("#nav-live").click();
    await expect(page.locator("#countdown")).not.toHaveText("--:--");
    await expect(page.locator("#intake-detail")).not.toHaveText("Build a plan");
    await saveView(page, "live");
  });

  await test.step("go offline and generate again", async () => {
    await page.locator("#nav-setup").click();
    await page.locator("#pane-ready").click();
    await expect(page.locator("#offline-ready-line")).toContainText("offline ready: yes", { timeout: 30000 });
    await page.waitForFunction(() => navigator.serviceWorker.controller);
    await page.locator("#pane-configure").click();
    await context.setOffline(true);
    // Reload with the radio off. The shell is served from cache, so a missing
    // network response cannot blank the page.
    await page.reload({ waitUntil: "load" });
    await expect(page.getByTestId("summary")).toContainText("Salomon Vitosha Trail 12 km", { timeout: 30000 });
    await page.locator("#pane-configure").click();
    await expect(page.getByTestId("build-plan")).toBeVisible();
    await page.locator(".gut[data-carbs='90']").click();
    await expect(page.locator(".gut[data-carbs='90']")).toHaveAttribute("aria-checked", "true");
    await page.getByTestId("build-plan").click();
    await expect(page.getByTestId("summary")).toContainText("/90 g/h", { timeout: PLAN_MS });
    await expect.poll(() => page.evaluate(() => window.__planCalls)).toBe(1);
    await expect(page.getByTestId("summary")).toContainText("Salomon Vitosha Trail 12 km");
    await expect(page.getByTestId("packing-list").locator(".pack-card").first()).toContainText("Cherni Vrah");
  });

  await test.step("recalibrate once from a mocked GPS fix", async () => {
    await context.grantPermissions(["geolocation"]);
    await context.setGeolocation(ON_COURSE);
    await page.locator("#nav-live").click();
    await page.getByTestId("sync-gps").click();
    await expect(page.locator("#gps-banner")).toContainText("GPS Locked");
    await expect(page.getByTestId("live-message")).not.toHaveText("", { timeout: PLAN_MS });
    await expect(page.getByTestId("live-message")).not.toContainText("not rebuilt");
    await expect(page.locator("#off-course")).toHaveClass(/hidden/);
  });

  await test.step("upload a custom GPX and generate", async () => {
    await page.locator("#nav-setup").click();
    await page.locator("#pane-configure").click();
    await page.getByTestId("gpx-file").setInputFiles(GPX);
    await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-12.gpx", { timeout: 60000 });
    await expect(page.getByTestId("race-select")).toBeDisabled();
    await page.getByTestId("build-plan").click();
    await expect(page.getByTestId("summary")).toContainText("Uploaded GPX", { timeout: PLAN_MS });
    await expect.poll(() => page.evaluate(() => window.__planCalls)).toBe(2);
    await expect(page.getByTestId("packing-list").locator(".pack-card").first()).toBeVisible();
    await page.locator("#nav-map").click();
    await expect.poll(async () => {
      const counts = await chartCounts(page);
      return counts.elevation > 1 && counts.stops > 0;
    }, { timeout: PLAN_MS }).toBe(true);
  });
});
