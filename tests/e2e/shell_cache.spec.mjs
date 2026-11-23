/**
 * The race-day shell must paint from cache while a refresh is stuck.
 * A network-first shell would sit on the stalled request and miss the deadline.
 *
 *   npx playwright test tests/e2e/shell_cache.spec.mjs
 */

import { expect, test } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

test("reloads a usable shell within 4s while app-shell requests stall", async ({ page, context }) => {
  test.setTimeout(120000);
  let releaseStall = () => {};
  const stallDone = new Promise((resolve) => {
    releaseStall = resolve;
  });

  try {
    await page.goto("/");
    await expect(page.getByTestId("gpx-drop")).toBeVisible({ timeout: 30000 });
    await page.waitForFunction(() => navigator.serviceWorker.controller, null, { timeout: 30000 });
    await expect.poll(async () => page.evaluate(async () => {
      const names = await caches.keys();
      const shell = names.find((name) => name.startsWith("race-fuel-v"));
      if (!shell) return false;
      const cache = await caches.open(shell);
      const index = await cache.match("/index.html");
      const app = await cache.match("/app.js");
      const races = await cache.match("/data/races.json");
      return Boolean(index && app && races);
    }), { timeout: 30000 }).toBe(true);

    const origin = new URL(page.url()).origin;
    const runtime = new Set(await page.evaluate(async () => {
      const manifest = await import("/runtime_manifest.js");
      return manifest.RUNTIME_FILES;
    }));

    const seen = [];
    let stalled = 0;
    await context.route("**/*", async (route) => {
      const url = new URL(route.request().url());
      seen.push(url.href);
      const shellRequest = url.origin === origin && !runtime.has(url.pathname);
      if (!shellRequest) {
        await route.continue();
        return;
      }
      stalled += 1;
      await stallDone;
      await route.abort();
    });

    const started = Date.now();
    await page.reload({ waitUntil: "commit", timeout: 4000 });
    const budget = () => Math.max(1, 4000 - (Date.now() - started));
    await expect(page.getByTestId("gpx-drop")).toBeVisible({ timeout: budget() });
    await expect(page.getByTestId("build-plan")).toBeEnabled();
    expect(Date.now() - started).toBeLessThan(4000);
    expect(stalled, `no stalled shell request; saw ${seen.join(" | ")}`).toBeGreaterThan(0);
  } finally {
    releaseStall();
  }
});
