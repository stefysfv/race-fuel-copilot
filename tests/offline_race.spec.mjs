/**
 * Load the app, wait until Pyodide is cached, drop the network, then build a
 * plan and recalibrate. Requires `frontend/engine.zip` and a static server.
 *
 *   npx playwright test tests/offline_race.spec.mjs
 */

import { expect, test } from "@playwright/test";

test("setup, map, checklist, and diagnostics are on the page", async ({ page }) => {
  await page.goto("/?diag=1");
  await expect(page.locator("#race-select")).toBeVisible();
  await expect(page.locator("#weight")).toBeEnabled();
  await expect(page.locator("#diag-panel")).toBeVisible();
  await expect(page.locator("#diag-copy")).toHaveText("Copy as JSON");
  await page.locator("#build-plan").click();
  await expect(page.locator("#build-plan")).toHaveText("Starting engine...");
  await page.locator("#pane-ready").click();
  await expect(page.locator("#ready-pane")).toContainText("Check this on Wi-Fi before race day.");
  await page.locator("#nav-map").click();
  await expect(page.locator("#map-empty")).toBeVisible();
});

test("builds and recalibrates with no signal", async ({ page, context }) => {
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 42.5632102570075, longitude: 23.2704473114883 });
  await page.goto("/");
  await expect(page.locator("#offline-prep")).toContainText("Ready for offline", { timeout: 180000 });
  await expect(page.locator("#race-select")).toContainText("27 km");
  await page.locator("#pane-ready").click();
  await expect(page.locator("#offline-ready-line")).toContainText("offline ready: yes", { timeout: 30000 });
  await page.locator("#pane-configure").click();

  await context.setOffline(true);
  await page.locator("#build-plan").click();
  await expect(page.locator("#packing-pane")).toContainText("Cherni Vrah", { timeout: 120000 });

  await page.locator("#nav-live").click();
  await page.locator("#gi-rescue").click();
  await expect(page.locator("#live-message")).not.toHaveText("", { timeout: 120000 });
  const message = await page.locator("#live-message").innerText();
  expect(message).not.toContain("not rebuilt");
});
