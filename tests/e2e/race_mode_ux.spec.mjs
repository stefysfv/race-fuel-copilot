/** Real Race Mode UX: 3-2-1-Go countdown, HUD, intake log, Pause/Resume, and Restart confirmation. */

import { expect, test } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

test("starts Race Mode with 3-2-1-GO, logs intake, pauses/resumes, and confirms restart", async ({ page }) => {
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

  await page.locator("#nav-live").click();
  await expect(page.getByTestId("race-hud")).toBeHidden();
  await expect(page.getByTestId("race-session-controls")).toBeHidden();

  // Start Race Mode -> 3-2-1-GO overlay appears
  await page.getByTestId("race-mode").click();
  await expect(page.getByTestId("race-countdown-overlay")).toBeVisible();
  await expect(page.getByTestId("race-countdown-num")).toHaveText(/^(3|2|1|GO!)$/);

  // Wait for GO! to complete and HUD + controls to be active
  await expect(page.getByTestId("race-countdown-overlay")).toBeHidden({ timeout: 8000 });
  await expect(page.getByTestId("race-hud")).toBeVisible();
  await expect(page.getByTestId("race-live-badge")).toHaveText("RACE LIVE");
  await expect(page.getByTestId("race-session-controls")).toBeVisible();
  await expect(page.getByTestId("race-log-card")).toBeVisible();

  // Log an intake -> shows in the live intake log
  await page.getByTestId("log-intake").click();
  await expect(page.getByTestId("race-log-list")).toContainText("00:00:");

  // Pause -> badge switches to PAUSED, button switches to Resume, elapsed freezes
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("PAUSED");
  await expect(page.getByTestId("race-pause")).toHaveText("Resume");
  const pausedClock = await page.getByTestId("race-elapsed").innerText();
  await page.waitForTimeout(1500);
  await expect(page.getByTestId("race-elapsed")).toHaveText(pausedClock);

  // Resume -> back to RACE LIVE
  await page.getByTestId("race-pause").click();
  await expect(page.getByTestId("race-live-badge")).toHaveText("RACE LIVE");
  await expect(page.getByTestId("race-pause")).toHaveText("Pause");

  // Restart -> confirmation modal opens; Cancel keeps run
  await page.getByTestId("race-restart").click();
  await expect(page.getByTestId("restart-modal")).toBeVisible();
  await expect(page.getByTestId("restart-modal")).toContainText("Are you sure you want to restart?");
  await page.getByTestId("cancel-restart").click();
  await expect(page.getByTestId("restart-modal")).toBeHidden();
  await expect(page.getByTestId("race-live-badge")).toHaveText("RACE LIVE");

  // Restart -> Confirm resets clock & log while keeping the built plan intact
  await page.getByTestId("race-restart").click();
  await page.getByTestId("confirm-restart").click();
  await expect(page.getByTestId("restart-modal")).toBeHidden();
  await expect(page.getByTestId("race-hud")).toBeHidden();
  await expect(page.getByTestId("log-intake")).toBeEnabled();
});
