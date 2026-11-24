import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

function haversineM(lat1, lon1, lat2, lon2) {
  const R = 6371000;
  const toRad = (d) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function pointAtKm(gpxFile, targetKm) {
  const xml = fs.readFileSync(gpxFile, "utf8");
  const pts = [];
  for (const m of xml.matchAll(/<trkpt\b([^>]*)>/g)) {
    const attrs = m[1];
    const lat = Number(attrs.match(/lat=["']([^"']+)["']/)?.[1]);
    const lon = Number(attrs.match(/lon=["']([^"']+)["']/)?.[1]);
    if (Number.isFinite(lat) && Number.isFinite(lon)) pts.push({ lat, lon });
  }
  let distM = 0;
  for (let i = 1; i < pts.length; i += 1) {
    distM += haversineM(pts[i - 1].lat, pts[i - 1].lon, pts[i].lat, pts[i].lon);
    if (distM >= targetKm * 1000) return { ...pts[i], actualKm: distM / 1000 };
  }
  throw new Error(`GPX shorter than ${targetKm} km (got ${(distM / 1000).toFixed(1)} km)`);
}

test.use({
  viewport: { width: 390, height: 844 },
  locale: "en-GB", // keeps the app in km (other specs do the same)
  serviceWorkers: "allow",
});

test("spoofed GPS check-ins archive passed sections, clear past map stops, and survive reload", async ({ page }) => {
  test.setTimeout(180000);

  const gpxPath = path.resolve("frontend/data/gpx/salomon-vitosha-40.gpx");
  const fix1 = pointAtKm(gpxPath, 18);
  const fix2 = pointAtKm(gpxPath, 33);

  await page.addInitScript(() => {
    window.__mockFix = null;
    window.__setMockGps = (lat, lon, timestampMs) => {
      window.__mockFix = { coords: { latitude: lat, longitude: lon, accuracy: 8 }, timestamp: timestampMs };
    };
    const geo = {
      getCurrentPosition: (ok, err) => {
        if (!window.__mockFix) { err?.({ code: 2, message: "No mock fix configured" }); return; }
        setTimeout(() => ok({ ...window.__mockFix }), 10);
      },
      watchPosition: () => 1,
      clearWatch: () => {},
    };
    Object.defineProperty(navigator, "geolocation", { value: geo, configurable: true });
  });

  await page.goto("/");
  await expect(page.getByTestId("offline-status")).toContainText("Ready for offline", { timeout: 120000 });

  // FIX 1: the name shows "Reading <file>" immediately; the GPX is only usable
  // once the worker finishes inspecting it ("Loaded <file>" + sanity card).
  await page.locator("#gpx-file").setInputFiles(gpxPath);
  await expect(page.getByTestId("gpx-name")).toContainText("Loaded salomon-vitosha-40.gpx", { timeout: 90000 });
  await expect(page.getByTestId("sanity-card")).toBeVisible();
  await expect(page.getByTestId("gpx-error")).toBeHidden();

  // FIX 2: the app already opens #aid-drawer, so clicking its <summary> CLOSED it
  // (that was the "element is not visible" on #auto-aids). Only open it if needed.
  const aidDrawer = page.locator("#aid-drawer");
  await expect(aidDrawer).toBeVisible();
  if (!(await aidDrawer.evaluate((el) => el.open))) {
    await aidDrawer.locator("summary").click();
  }
  await page.locator("#auto-aids").click();
  // For a 40 km course the app spaces stations every 12 km (15 km only at 70+ km).
  await expect(page.locator("#aid-list li").first()).toBeVisible();

  await page.getByTestId("build-plan").click();
  await expect(page.getByTestId("packing-list")).not.toBeEmpty({ timeout: 60000 });

  await page.locator("#nav-map").click();
  const initialMapStops = await page.locator("#stop-list li").count();
  console.log(`[Start] Upcoming map stops: ${initialMapStops}`);
  expect(initialMapStops).toBeGreaterThan(2);

  await page.locator("#nav-live").click();
  await page.getByTestId("start-race-cta").click();
  await expect(page.getByTestId("race-countdown-overlay")).toBeHidden({ timeout: 10000 });
  const initialSection = await page.getByTestId("current-section-title").innerText();
  console.log(`[Start] Active section: ${initialSection}`);
  await expect(page.getByTestId("live-completed-details")).toBeHidden();

  const t0 = Date.now();
  await page.evaluate(({ lat, lon, ts }) => window.__setMockGps(lat, lon, ts), { lat: fix1.lat, lon: fix1.lon, ts: t0 });
  await page.getByTestId("sync-gps").click();
  await expect(page.getByTestId("live-progress-line")).toContainText(/1[789]\.\d\s*km passed/, { timeout: 30000 });

  const afterFix1Section = await page.getByTestId("current-section-title").innerText();
  const archive = page.getByTestId("live-completed-details");
  await expect(archive).toBeVisible();
  await archive.locator("summary").click();
  const archiveText1 = await archive.innerText();
  console.log(`[GPS #1 @ 18 km] Active: ${afterFix1Section}\nArchived:\n${archiveText1}`);
  expect(afterFix1Section).not.toBe(initialSection);
  expect(archiveText1).toContain("Start");

  await page.locator("#nav-map").click();
  const stopsAfterFix1 = await page.locator("#stop-list li").count();
  console.log(`[GPS #1] Upcoming map stops: ${stopsAfterFix1} (was ${initialMapStops})`);
  expect(stopsAfterFix1).toBeLessThan(initialMapStops);

  await page.locator("#nav-live").click();
  await page.evaluate(({ lat, lon, ts }) => window.__setMockGps(lat, lon, ts), { lat: fix2.lat, lon: fix2.lon, ts: t0 + 2 * 3600 * 1000 });
  await page.getByTestId("sync-gps").click();
  await expect(page.getByTestId("live-progress-line")).toContainText(/3[234]\.\d\s*km passed/, { timeout: 30000 });

  const afterFix2Section = await page.getByTestId("current-section-title").innerText();
  if (!(await archive.evaluate((el) => el.open))) await archive.locator("summary").click();
  const archiveText2 = await archive.innerText();
  console.log(`[GPS #2 @ 33 km] Active: ${afterFix2Section}\nArchived:\n${archiveText2}`);
  const expectedSection = "Current position → Подкрепителен Пункт";
  expect(afterFix2Section).toBe(expectedSection);
  await expect(page.getByTestId("current-eta-sub")).toContainText("At 36.4 km on course");

  const archivedRows = archive.locator("#live-completed-list li");
  const beforeReload = await archivedRows.allTextContents();
  expect(archiveText2).toContain("Start");
  expect(archiveText2).toContain("Aid 1");

  await page.reload();
  await page.locator("#nav-live").click();
  await expect(page.getByTestId("live-progress-line")).toContainText(/3[234]\.\d\s*km passed/, { timeout: 20000 });
  await expect(page.getByTestId("live-completed-details")).toBeVisible();
  await expect(page.getByTestId("current-section-title")).toHaveText(expectedSection);
  if (!(await archive.evaluate((el) => el.open))) {
    await archive.locator("summary").click();
  }
  await expect(archivedRows).toHaveText(beforeReload);
  console.log(`[Reload] Same active section and all ${beforeReload.length} archived rows persisted.`);
});
