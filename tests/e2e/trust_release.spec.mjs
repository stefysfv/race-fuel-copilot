/**
- Prompt 19: Privacy/Terms footer links, OG metadata, landing & How It Works,
- version & Changelog / What's New on update, and opt-in Copy Diagnostics.
 */

import { expect, test } from "@playwright/test";

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

test("exposes privacy, terms, OG metadata, How It Works, changelog, what's new, and opt-in diagnostics", async ({ page }) => {
  await page.goto("/");

  const ogTitle = await page.locator("meta[property='og:title']").getAttribute("content");
  expect(ogTitle).toContain("Race Fuel Co-Pilot");

  await expect(page.getByTestId("landing-intro")).toBeVisible();
  await page.locator("[data-testid='how-it-works'] summary").click();
  await expect(page.getByTestId("how-it-works")).toContainText("Green");
  await expect(page.getByTestId("physiology-link")).toHaveAttribute("href", /physiology-assumptions\.md/);

  await expect(page.getByTestId("footer-privacy")).toHaveAttribute("href", "/privacy.html");
  await expect(page.getByTestId("footer-terms")).toHaveAttribute("href", "/terms.html");
  await page.getByTestId("footer-changelog").click();
  await expect(page.getByTestId("changelog-modal")).toBeVisible();
  await expect(page.getByTestId("changelog-modal")).toContainText("v1.0.0");
  await page.locator("#close-changelog").click();
  await expect(page.getByTestId("changelog-modal")).toBeHidden();

  await page.evaluate(() => localStorage.setItem("fuel-seen-version", "0.9.0"));
  await page.reload();
  await expect(page.getByTestId("whats-new-banner")).toBeVisible();
  await page.getByTestId("whats-new-dismiss").click();
  await expect(page.getByTestId("whats-new-banner")).toBeHidden();

  const posts = [];
  page.on("request", (req) => {
    if (req.method() === "POST") posts.push(req.url());
  });
  await page.getByTestId("optin-copy-diag").click();
  await expect(page.getByTestId("optin-diag-note")).not.toHaveText("");
  expect(posts).toEqual([]);

  await page.goto("/privacy.html");
  await expect(page.locator("h1")).toContainText("Privacy Policy");
  await expect(page.locator("body")).toContainText("on your phone");

  await page.goto("/terms.html");
  await expect(page.locator("h1")).toContainText("Not Medical Advice");
});
