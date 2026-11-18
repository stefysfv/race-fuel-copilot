/**
 * Load the app, wait until Pyodide is cached, drop the network, then build a
 * plan and recalibrate. Requires `frontend/engine.zip` and a static server.
 *
 *   npx playwright test tests/offline_race.spec.mjs
 */

import { expect, test } from "@playwright/test";

test("builds and recalibrates with no signal", async ({ page, context }) => {
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 42.5632102570075, longitude: 23.2704473114883 });
  await page.goto("/");
  await expect(page.locator("#offline-prep")).toContainText("Ready for offline", { timeout: 180000 });
  await expect(page.locator("#race-select")).toContainText("27 km");

  await context.setOffline(true);
  await page.locator("#build-plan").click();
  await expect(page.locator("#packing-pane")).toContainText("Cherni Vrah", { timeout: 120000 });

  await page.locator("#nav-live").click();
  await page.locator("#gi-rescue").click();
  await expect(page.locator("#live-message")).not.toHaveText("", { timeout: 120000 });
  const message = await page.locator("#live-message").innerText();
  expect(message).not.toContain("not rebuilt");
});
