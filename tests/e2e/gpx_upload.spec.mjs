/**
 * Custom GPX upload: sanity card, a waypoint, a recent course, and the
 * three failures an athlete hits most often.
 */

import { expect, test } from "@playwright/test";

const METERS_PER_DEG = (6371000 * Math.PI) / 180;

test.use({
  viewport: { width: 390, height: 844 },
  locale: "en-GB",
  serviceWorkers: "allow",
});

function trackXml({ points, stepM, name, waypoint }) {
  let lat = 42.5;
  const step = stepM / METERS_PER_DEG;
  const rows = [];
  for (let index = 0; index < points; index += 1) {
    rows.push(`<trkpt lat="${lat.toFixed(6)}" lon="23.300000"><ele>${1000 + index}</ele></trkpt>`);
    lat += step;
  }
  const aid = waypoint
    ? `<wpt lat="${(42.5 + waypoint.index * step).toFixed(6)}" lon="23.300000"><name>${waypoint.name}</name></wpt>`
    : "";
  return `<?xml version="1.0"?><gpx version="1.1">${aid}<trk><name>${name}</name><trkseg>${rows.join("")}</trkseg></trk></gpx>`;
}

async function chooseFile(page, name, contents) {
  await page.getByTestId("gpx-file").setInputFiles({
    name,
    mimeType: "application/gpx+xml",
    buffer: Buffer.from(contents),
  });
}

test("rejects a file that is not GPX, a short track, and an oversized file", async ({ page }) => {
  await page.goto("/");
  await chooseFile(page, "huge.gpx", "x".repeat(8_000_001));
  await expect(page.getByTestId("gpx-error")).toHaveText("This file is too large. Keep it under 8 MB.");
  await expect(page.getByTestId("sanity-card")).toBeHidden();

  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 180000 });
  await chooseFile(page, "notes.gpx", "<gpx><trk><trkpt></gpx>");
  await expect(page.getByTestId("gpx-error")).toContainText("not valid GPX", { timeout: 60000 });

  await chooseFile(page, "short.gpx", trackXml({ points: 10, stepM: 20, name: "Short" }));
  await expect(page.getByTestId("gpx-error")).toContainText("at least 50 points", { timeout: 60000 });
});

test("loads a custom GPX, edits an aid, builds a plan, and keeps the course", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("gpx-drop")).toBeVisible();
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 180000 });

  const xml = trackXml({
    points: 70,
    stepM: 40,
    name: "Creek loop",
    waypoint: { index: 20, name: "AS1" },
  });
  await chooseFile(page, "creek-loop.gpx", xml);
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded creek-loop.gpx", { timeout: 60000 });
  await expect(page.getByTestId("sanity-card")).toBeVisible();
  await expect(page.getByTestId("sanity-card")).toContainText("Does this look right?");
  await expect(page.getByTestId("sanity-distance")).not.toHaveText("");
  await expect(page.getByTestId("sanity-gain")).toBeVisible();
  await expect(page.getByTestId("sanity-loss")).toBeVisible();
  await expect(page.getByTestId("sanity-high")).toBeVisible();
  await expect(page.getByTestId("sanity-profile")).toBeVisible();

  const aidName = page.locator("#waypoint-list [data-role='name']");
  await expect(aidName).toHaveValue("AS1");
  await expect(page.locator("#waypoint-list [data-role='check']")).toBeChecked();
  await aidName.fill("Creek aid");
  await page.getByTestId("distance-override").fill("9");
  await expect(page.getByTestId("sanity-scale")).toHaveText("The plan will use 9.0 km.");

  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("summary")).toContainText("Uploaded GPX", { timeout: 180000 });
  await expect(page.getByTestId("packing-list")).toContainText("Creek aid");

  await page.reload();
  await page.locator("#pane-configure").click();
  await expect(page.getByTestId("recent-course")).toHaveText("creek-loop.gpx", { timeout: 30000 });
});
