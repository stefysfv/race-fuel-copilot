import { expect, test } from "@playwright/test";

test("vest and aid station food search dropdowns open, filter, and add items without errors", async ({ page }) => {
  const errors = [];
  page.on("pageerror", (err) => errors.push(err.message));

  await page.goto("/");
  await page.waitForSelector("#vest-drawer");

  // 1. Vest food browser: focus shows suggestions, typing "maurten" filters & adds
  await page.locator("#vest-drawer > summary").click();
  const vestInput = page.locator("#vest-custom-name");
  await vestInput.focus();
  await expect(page.locator("#vest-suggestions .food-suggestion-row").first()).toBeVisible();

  await vestInput.fill("maurten");
  const maurtenRow = page.locator("#vest-suggestions .food-suggestion-row", { hasText: "Maurten Gel 100" }).first();
  await expect(maurtenRow).toBeVisible();
  await maurtenRow.click();
  await expect(page.locator("#vest-foods")).toContainText("Maurten Gel 100");

  // 2. Aid station food browser: typing "broth" & "tomato" filters & adds
  await page.locator("#station-drawer > summary").click();
  const stationInput = page.locator("#station-custom-name");
  await stationInput.fill("broth");
  const brothRow = page.locator("#station-suggestions .food-suggestion-row", { hasText: "Warm Broth" }).first();
  await expect(brothRow).toBeVisible();
  await brothRow.click();
  await expect(page.locator("#station-foods")).toContainText("Warm Broth");

  await stationInput.fill("tomato");
  const tomatoRow = page.locator("#station-suggestions .food-suggestion-row", { hasText: "Salted Tomato" }).first();
  await expect(tomatoRow).toBeVisible();
  await tomatoRow.click();
  await expect(page.locator("#station-foods")).toContainText("Salted Tomato");

  expect(errors).toEqual([]);
});
