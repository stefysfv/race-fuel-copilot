/** Adaptive check-in guide: Launch -> 3-2-1-GO -> Cockpit -> GPS clear -> Live Crew Sheet -> Recap -> Feedback. */

import { expect, test } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

test("runs Launch -> 3-2-1-GO -> forward-only legs -> Live Crew Sheet -> Pause/Restart -> Finish Recap", async ({ page }) => {
  test.setTimeout(180000);
  await page.addInitScript(() => {
    const pos = {
      coords: { latitude: 42.563584, longitude: 23.278586, accuracy: 8 },
      timestamp: Date.now(),
    };
    const geo = {
      getCurrentPosition: (ok) => setTimeout(() => ok({ ...pos, timestamp: Date.now() }), 10),
      watchPosition: (ok) => {
        setTimeout(() => ok({ ...pos, timestamp: Date.now() }), 10);
        return 1;
      },
      clearWatch: () => {},
    };
    Object.defineProperty(navigator, "geolocation", { value: geo, configurable: true });
  });

  await page.goto("/");
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 120000 });
  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("packing-list")).not.toBeEmpty({ timeout: 60000 });

  // Pre-race Launch screen: only launch card visible, cockpit hidden.
  await page.locator("#nav-live").click();
  await expect(page.getByTestId("race-launch")).toBeVisible();
  await expect(page.getByTestId("race-cockpit")).toBeHidden();

  // Start Race -> 3-2-1-GO overlay -> Active Race Cockpit.
  await page.getByTestId("start-race-cta").click();
  await expect(page.getByTestId("race-countdown-overlay")).toBeVisible();
  await expect(page.getByTestId("race-countdown-num")).toHaveText(/^(3|2|1|GO!)$/);
  await expect(page.getByTestId("race-countdown-overlay")).toBeHidden({ timeout: 8000 });
  await expect(page.getByTestId("race-launch")).toBeHidden();
  await expect(page.getByTestId("race-cockpit")).toBeVisible();
  await expect(page.getByTestId("race-live-badge")).toHaveText("RACE LIVE");

  // Wait for GPS check-in (snapped near Cherni Vrah ~8.2 km) -> progress line & Live Crew Sheet update.
  await expect(page.getByTestId("live-progress-line")).toContainText("passed", { timeout: 30000 });
  await page.getByTestId("live-crew-btn").click();
  await expect(page.getByTestId("crew-live-banner")).toContainText("Live GPS updated");
  await page.locator("#close-crew").click();

  // Log an intake & clear a section ahead.
  await page.getByTestId("log-intake").click();
  await expect(page.getByTestId("race-log-list")).toContainText("00:00:");
  const beforeAhead = await page.getByTestId("ahead-leg-card").count();
  if (beforeAhead > 0) {
    await page.getByTestId("mark-section-done").first().click();
    await expect(page.getByTestId("ahead-leg-card")).toHaveCount(beforeAhead - 1);
    await expect(page.getByTestId("live-completed-details")).toBeVisible();
  }

  // Finish Run -> Section-by-section Race Recap -> handoff to Feedback.
  await page.getByTestId("open-recap-live").click();
  await expect(page.getByTestId("recap-modal")).toBeVisible();
  await expect(page.getByTestId("recap-summary")).toContainText("1 logged");
  await expect(page.getByTestId("recap-leg").first()).toBeVisible();
  await page.getByTestId("recap-to-feedback").click();
  await expect(page.getByTestId("feedback-notes")).toHaveValue(/Logged intakes:/);

  // Back to Live -> Pause/Resume and Restart confirmation.
  await page.locator("#nav-live").click();
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("PAUSED");
  const pausedClock = await page.getByTestId("race-elapsed").innerText();
  await page.waitForTimeout(1300);
  await expect(page.getByTestId("race-elapsed")).toHaveText(pausedClock);
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("RACE LIVE");

  await page.getByTestId("race-restart").click();
  await expect(page.getByTestId("restart-modal")).toContainText("Are you sure you want to restart?");
  await page.getByTestId("cancel-restart").click();
  await expect(page.getByTestId("race-cockpit")).toBeVisible();

  await page.getByTestId("race-restart").click();
  await page.getByTestId("confirm-restart").click();
  await expect(page.getByTestId("race-launch")).toBeVisible();
  await expect(page.getByTestId("race-cockpit")).toBeHidden();
});
