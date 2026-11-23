/**
 * Install card and post-race feedback. These checks stay on the shell:
 * they do not wait for the fueling engine.
 */

import { expect, test } from "@playwright/test";

const DAY_MS = 24 * 60 * 60 * 1000;
const DISMISS_KEY = "fuel-install-dismissed-at";
const VERIFIED_KEY = "fuel-offline-verified-at";
const FEEDBACK_KEY = "fuel-feedback";
const STALE_TEXT = "Open this app on Wi-Fi to confirm it still works offline.";
const RACE = "Salomon Vitosha Trail 27 km";

test.use({
  viewport: { width: 390, height: 844 },
  serviceWorkers: "allow",
});

async function appReady(page) {
  await expect(page.getByTestId("feedback-preview")).toContainText(
    "Nothing is sent until you press Send feedback.",
  );
}

test("shows the install card in a browser and keeps a dismissal for 7 days", async ({ page }) => {
  await page.goto("/");
  await appReady(page);
  const card = page.getByTestId("install-banner");
  const steps = page.getByTestId("install-steps");
  await expect(card).toBeVisible();
  await expect(steps).toContainText("Use the browser menu to install this site");
  await expect(steps).not.toContainText("Share > Add to Home Screen");

  await page.getByTestId("install-dismiss").click();
  await expect(card).toBeHidden();
  await page.reload();
  await appReady(page);
  await expect(card).toBeHidden();

  await page.evaluate(({ key, age }) => {
    localStorage.setItem(key, String(Date.now() - age));
  }, { key: DISMISS_KEY, age: 6 * DAY_MS });
  await page.reload();
  await appReady(page);
  await expect(card).toBeHidden();

  await page.evaluate(({ key, age }) => {
    localStorage.setItem(key, String(Date.now() - age));
  }, { key: DISMISS_KEY, age: 8 * DAY_MS });
  await page.reload();
  await appReady(page);
  await expect(card).toBeVisible();
  await expect(steps).toContainText("Home Screen");
});

test.describe("installed display mode", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      const original = window.matchMedia.bind(window);
      window.matchMedia = (query) => {
        if (String(query).includes("display-mode: standalone")) {
          return {
            matches: true,
            media: String(query),
            addEventListener() {},
            removeEventListener() {},
            addListener() {},
            removeListener() {},
            dispatchEvent() { return false; },
            onchange: null,
          };
        }
        return original(query);
      };
    });
  });

  test("hides the install card", async ({ page }) => {
    await page.goto("/");
    await appReady(page);
    await expect(page.getByTestId("install-banner")).toBeHidden();
    await page.locator("#pane-ready").click();
    await expect(page.getByTestId("ready-installed")).toHaveText("Installed to Home Screen: Yes");
  });
});

test.describe("iPhone Safari", () => {
  test.use({
    userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  });

  test("names Share > Add to Home Screen", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("install-steps")).toHaveText(
      "On iPhone or iPad, open this page in Safari and tap Share > Add to Home Screen.",
    );
  });
});

test.describe("Android Chrome", () => {
  test.use({
    userAgent: "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
  });

  test("names Install app", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("install-steps")).toHaveText(
      "On Android, open the Chrome menu and tap Install app.",
    );
  });
});

test("states when cached files can be evicted and when the last check is stale", async ({ page }) => {
  await page.goto("/");
  await appReady(page);
  await page.locator("#pane-ready").click();
  await expect(page.getByTestId("ready-installed")).toHaveText("Installed to Home Screen: No");
  const storage = page.getByTestId("ready-storage");
  await expect(storage).toHaveText(/Storage protected: (Yes|No)/);
  const protectedStorage = (await storage.innerText()).includes("Yes");
  if (protectedStorage) {
    await expect(page.getByTestId("ready-evict")).toBeHidden();
  } else {
    await expect(page.getByTestId("ready-evict")).toHaveText(
      "The browser may evict cached files after inactivity. Install the app to the Home Screen and open it every few days.",
    );
  }
  await expect(page.getByTestId("ready-verified")).toContainText("Last verified offline:");

  await page.evaluate(({ key, age }) => {
    localStorage.setItem(key, String(Date.now() - age));
  }, { key: VERIFIED_KEY, age: 15 * DAY_MS });
  await page.reload();
  await appReady(page);
  await expect(page.getByTestId("offline-stale")).toHaveText(STALE_TEXT);

  await page.goto("/?diag=1");
  await appReady(page);
  const diag = page.locator("#diag-list");
  await expect(diag).toContainText("Last verified offline");
  await expect(diag).toContainText("First offline setup");
  await expect(diag).toContainText("Bytes cached");
  await expect(diag).toContainText("Cache retry");
  await expect(diag).toContainText("Installed to Home Screen");
});

test("saves feedback on the phone and builds the send link without a POST", async ({ page }) => {
  const posts = [];
  page.on("request", (request) => {
    if (request.method() === "POST") posts.push(request.url());
  });

  await page.goto("/");
  await page.getByTestId("nav-feedback").click();
  await page.getByTestId("feedback-race").fill(RACE);
  await expect(page.getByTestId("feedback-preview")).toContainText("race name");
  await expect(page.getByTestId("feedback-preview")).toContainText("GPS tracks");
  await expect(page.getByTestId("feedback-preview")).toContainText("analytics");
  await expect(page.getByTestId("feedback-preview")).toContainText("account");

  await page.getByTestId("feedback-finish").fill("4:12 vs 4:05 predicted");
  await page.getByTestId("feedback-gi-severe").check();
  await page.getByTestId("feedback-changed").fill("Moved the second gel later");
  await page.getByTestId("feedback-notes").fill("Felt better after the second climb.");
  await page.getByTestId("feedback-save").click();

  const stored = await page.evaluate((key) => JSON.parse(localStorage.getItem(key)), FEEDBACK_KEY);
  expect(stored).toHaveLength(1);
  expect(stored[0].raceName).toBe(RACE);
  expect(stored[0].finishNote).toBe("4:12 vs 4:05 predicted");
  expect(stored[0].gi).toBe("severe");
  expect(stored[0].changed).toBe("Moved the second gel later");
  expect(stored[0].notes).toBe("Felt better after the second climb.");

  const links = await page.evaluate(async (key) => {
    const mod = await import("/feedback.js");
    const entry = JSON.parse(localStorage.getItem(key))[0];
    return {
      live: document.querySelector("#send-feedback").getAttribute("href"),
      built: mod.feedbackHref(entry),
      form: mod.feedbackHref(entry, { formUrl: "https://example.com/feedback" }),
      mail: mod.feedbackHref(entry, { email: "coach@example.com", formUrl: "" }),
    };
  }, FEEDBACK_KEY);
  expect(links.live).toBe(links.built);
  expect(links.live.startsWith("mailto:?")).toBe(false);
  expect(/^mailto:[^?]+\?subject=/.test(links.live) || /^https?:\/\//.test(links.live)).toBe(true);
  const mailBody = decodeURIComponent(links.live);
  expect(mailBody).toContain("Race name: Salomon Vitosha Trail 27 km");
  expect(mailBody).toContain("Finish versus predicted time: 4:12 vs 4:05 predicted");
  expect(mailBody).toContain("GI trouble: severe");
  expect(mailBody).toContain("What you changed: Moved the second gel later");
  expect(mailBody).toContain("Notes: Felt better after the second climb.");
  expect(mailBody.toLowerCase()).not.toContain("gpx");
  expect(mailBody.toLowerCase()).not.toContain("latitude");

  const form = new URL(links.form);
  expect(`${form.origin}${form.pathname}`).toBe("https://example.com/feedback");
  expect(form.searchParams.get("race")).toBe(RACE);
  expect(form.searchParams.get("finish")).toBe("4:12 vs 4:05 predicted");
  expect(form.searchParams.get("gi")).toBe("severe");
  expect(form.searchParams.get("changed")).toBe("Moved the second gel later");
  expect(form.searchParams.get("notes")).toBe("Felt better after the second climb.");
  expect(links.mail.startsWith("mailto:coach@example.com?")).toBe(true);

  await page.reload();
  await page.getByTestId("nav-feedback").click();
  await expect(page.getByTestId("feedback-entry")).toContainText(RACE);
  await page.getByTestId("feedback-entry").click();
  await expect(page.getByTestId("feedback-notes")).toHaveValue("Felt better after the second climb.");
  await expect(page.getByTestId("feedback-gi-severe")).toBeChecked();

  await page.evaluate(() => {
    document.querySelector("#send-feedback").addEventListener("click", (event) => {
      event.preventDefault();
    }, true);
  });
  await page.getByTestId("send-feedback").click();
  await page.getByTestId("feedback-copy").click();
  await expect(page.locator("#feedback-status")).toContainText(/Copied feedback|Could not copy/);
  expect(posts).toEqual([]);

  await page.locator("#nav-setup").click();
  await page.locator("#pane-packing").click();
  await page.getByTestId("open-feedback").click();
  await expect(page.getByTestId("feedback-race")).toBeVisible();
  await expect(page.getByTestId("feedback-notes")).toHaveValue("Felt better after the second climb.");
});
