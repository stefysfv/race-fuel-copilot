import path from "node:path";
/**
- Prompt 16: Metric/Imperial units (round-trip + identical engine request)
- and print/shareable crew sheet with +/-10% ETA bands and GI rescue steps.
 */

import { expect, test } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  locale: "en-US",
  serviceWorkers: "allow",
});

test("round-trips every unit conversion and sends an identical engine request in metric vs imperial", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("unit-imperial")).toHaveAttribute("aria-checked", "true");
  await expect(page.locator("#weight-label")).toHaveText("Weight (lb)");
  await expect(page.locator("#temp-label")).toHaveText("Temp (°F)");
  await expect(page.locator("#distance-override-label")).toContainText("mi");
  await page.locator("#gpx-file").setInputFiles(path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx"));
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-12.gpx", { timeout: 60000 });

  const results = await page.evaluate(async () => {
    const u = await import("/units.js");
    const pr = await import("/plan_request.js");

    const checks = {
      detectUS: u.detectUnitSystem(["en-US"]),
      detectGB: u.detectUnitSystem(["en-GB"]),
      detectBG: u.detectUnitSystem(["bg-BG"]),
      distErr: Math.abs(u.milesToKm(u.kmToMiles(42.195)) - 42.195),
      eleErr: Math.abs(u.feetToMeters(u.metersToFeet(2290)) - 2290),
      tempErr: Math.abs(u.fahrenheitToCelsius(u.celsiusToFahrenheit(18)) - 18),
      tempNegErr: Math.abs(u.fahrenheitToCelsius(u.celsiusToFahrenheit(-10)) - (-10)),
      weightErr: Math.abs(u.poundsToKg(u.kgToPounds(70)) - 70),
      paceErr: Math.abs(u.minPerMileToMinPerKm(u.minPerKmToMinPerMile(5.5)) - 5.5),
    };

    u.setUnitSystem("metric");
    const weightInput = document.querySelector("#weight");
    const tempInput = document.querySelector("#temperature");
    weightInput.value = "70";
    weightInput.dispatchEvent(new Event("input", { bubbles: true }));
    tempInput.value = "18";
    tempInput.dispatchEvent(new Event("input", { bubbles: true }));
    const metricBody = pr.courseBody(pr.readAthlete(8));

    u.setUnitSystem("imperial");
    const toggledBody = pr.courseBody(pr.readAthlete(8));

    weightInput.value = String(u.kgToPounds(70));
    weightInput.dispatchEvent(new Event("input", { bubbles: true }));
    tempInput.value = String(u.celsiusToFahrenheit(18));
    tempInput.dispatchEvent(new Event("input", { bubbles: true }));
    const retypedImperialBody = pr.courseBody(pr.readAthlete(8));

    return { checks, metricBody, toggledBody, retypedImperialBody };
  });

  expect(results.checks.detectUS).toBe("imperial");
  expect(results.checks.detectGB).toBe("metric");
  expect(results.checks.detectBG).toBe("metric");
  expect(results.checks.distErr).toBeLessThan(1e-9);
  expect(results.checks.eleErr).toBeLessThan(1e-9);
  expect(results.checks.tempErr).toBeLessThan(1e-9);
  expect(results.checks.tempNegErr).toBeLessThan(1e-9);
  expect(results.checks.weightErr).toBeLessThan(1e-9);
  expect(results.checks.paceErr).toBeLessThan(1e-9);

  // Toggling units must send the exact same engine request payload.
  expect(results.toggledBody).toEqual(results.metricBody);
  // Entering equivalent imperial values must produce the same metric athlete fields.
  expect(results.retypedImperialBody.athlete.weight_kg).toBeCloseTo(70, 6);
  expect(results.retypedImperialBody.athlete.temperature_c).toBeCloseTo(18, 6);
  expect({
    ...results.retypedImperialBody,
    athlete: {
      ...results.retypedImperialBody.athlete,
      weight_kg: Math.round(results.retypedImperialBody.athlete.weight_kg * 1e6) / 1e6,
      temperature_c: Math.round(results.retypedImperialBody.athlete.temperature_c * 1e6) / 1e6,
    },
  }).toEqual(results.metricBody);

  // Persisted toggle survives reload.
  await page.getByTestId("unit-metric").click();
  await expect(page.locator("#weight-label")).toHaveText("Weight (kg)");
  await page.reload();
  await expect(page.getByTestId("unit-metric")).toHaveAttribute("aria-checked", "true");
  await expect(page.locator("#weight-label")).toHaveText("Weight (kg)");
});

test("generates a plan, opens the crew sheet, renders ETA bands, hand-over, GI rescue, and plain-text share", async ({ page }) => {
  test.setTimeout(240000);
  await page.goto("/");
  await page.getByTestId("unit-metric").click();
  await page.locator("#gpx-file").setInputFiles(path.resolve("frontend/data/gpx/salomon-vitosha-12.gpx"));
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-12.gpx", { timeout: 60000 });
  await page.locator("#aid-name").fill("Cherni Vrah");
  await page.locator("#aid-km").fill("4");
  await page.locator("#add-aid").click();
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 180000 });

  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("summary")).not.toBeEmpty({ timeout: 180000 });

  await page.getByTestId("crew-sheet-btn").click();
  await expect(page.getByTestId("crew-sheet").getByRole("heading")).toContainText("salomon-vitosha-12.gpx");

  const rows = page.getByTestId("crew-row");
  const count = await rows.count();
  expect(count).toBeGreaterThanOrEqual(2);
  await expect(page.getByTestId("crew-table")).toContainText("Cherni Vrah");
  await expect(page.getByTestId("crew-table")).toContainText("Fast (−10%)");
  await expect(page.getByTestId("crew-table")).toContainText("Plan ETA");
  await expect(page.getByTestId("crew-table")).toContainText("Slow (+10%)");

  const fast = await page.getByTestId("crew-eta-fast").first().innerText();
  const plan = await page.getByTestId("crew-eta-plan").first().innerText();
  const slow = await page.getByTestId("crew-eta-slow").first().innerText();
  expect(fast).not.toBe(plan);
  expect(slow).not.toBe(plan);

  await expect(page.getByTestId("crew-gi-rescue")).toContainText("Stop solid food");
  await expect(page.getByTestId("crew-print")).toBeVisible();

  // Disable navigator.share so the plain-text fallback runs cleanly in headless Chromium.
  await page.evaluate(() => {
    Object.defineProperty(navigator, "share", { value: undefined, configurable: true });
  });
  await page.getByTestId("crew-share").click();
  await expect(page.getByTestId("crew-plain-text")).toBeVisible();
  const plain = await page.getByTestId("crew-plain-text").inputValue();
  expect(plain).toContain("CREW SHEET: salomon-vitosha-12.gpx");
  expect(plain).toContain("Fast (-10%)");
  expect(plain).toContain("Slow (+10%)");
  expect(plain).toContain("Cherni Vrah");
  expect(plain).toContain("GI RESCUE");
});
