/**
 * Prove the browser Pyodide worker produces the same plans as tests/golden.
 * Loads cases and strip_keys from tests/golden/cases.json. Never edits goldens.
 */

import fs from "node:fs";
import path from "node:path";

import { expect, test } from "@playwright/test";

const GOLDEN_DIR = path.resolve("tests/golden");
const MANIFEST = JSON.parse(fs.readFileSync(path.join(GOLDEN_DIR, "cases.json"), "utf8"));
const STRIP_KEYS = new Set(MANIFEST.strip_keys);
const CASES = MANIFEST.cases;
const TOLERANCE = 1e-6;
const DIFF_LIMIT = 12;

function stripKeys(value) {
  if (Array.isArray(value)) return value.map(stripKeys);
  if (value && typeof value === "object") {
    const out = {};
    for (const [k, v] of Object.entries(value)) {
      if (!STRIP_KEYS.has(k)) out[k] = stripKeys(v);
    }
    return out;
  }
  return value;
}

function preview(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  if (text && text.length > 160) return `${text.slice(0, 157)}...`;
  return String(text);
}

function diffPaths(expected, actual, trail = "$") {
  if (expected === null && actual === null) return [];
  if (expected === null || actual === null || typeof expected !== typeof actual) {
    return [[trail, expected, actual]];
  }
  if (Array.isArray(expected) || Array.isArray(actual)) {
    if (!Array.isArray(expected) || !Array.isArray(actual)) {
      return [[trail, expected, actual]];
    }
    const found = [];
    if (expected.length !== actual.length) {
      found.push([`${trail}.length`, expected.length, actual.length]);
    }
    const n = Math.min(expected.length, actual.length);
    for (let i = 0; i < n; i += 1) {
      found.push(...diffPaths(expected[i], actual[i], `${trail}[${i}]`));
    }
    return found;
  }
  if (typeof expected === "object") {
    const found = [];
    const keys = [...new Set([...Object.keys(expected), ...Object.keys(actual)])];
    for (const key of keys) {
      const child = `${trail}.${key}`;
      if (!(key in expected)) {
        found.push([child, "<missing>", actual[key]]);
      } else if (!(key in actual)) {
        found.push([child, expected[key], "<missing>"]);
      } else {
        found.push(...diffPaths(expected[key], actual[key], child));
      }
    }
    return found;
  }
  if (typeof expected === "number") {
    if (!Number.isFinite(expected) || !Number.isFinite(actual)) {
      return [[trail, expected, actual]];
    }
    if (Number.isInteger(expected) && Number.isInteger(actual)) {
      return expected === actual ? [] : [[trail, expected, actual]];
    }
    if (Math.abs(expected - actual) > TOLERANCE) {
      return [[trail, expected, actual]];
    }
    return [];
  }
  if (expected !== actual) {
    return [[trail, expected, actual]];
  }
  return [];
}

function formatDiff(name, diffs) {
  const shown = diffs.slice(0, DIFF_LIMIT);
  const lines = [`Pyodide worker differs from golden: ${name}`, `${diffs.length} path(s) differ.`];
  for (const [p, exp, act] of shown) {
    lines.push(`  ${p}`);
    lines.push(`    snapshot: ${preview(exp)}`);
    lines.push(`    actual:   ${preview(act)}`);
  }
  if (diffs.length > shown.length) {
    lines.push(`  ... ${diffs.length - shown.length} more path(s)`);
  }
  return lines.join("\n");
}

test("Pyodide worker matches every golden snapshot in tests/golden/cases.json", async ({ page }) => {
  test.setTimeout(240000);
  await page.goto("/");

  // Assert worker mode (never HTTP) and boot the real Pyodide worker once.
  const mode = await page.evaluate(async () => {
    const client = await import("/engine_client.js");
    await client.prepareOffline();
    return client.ENGINE_MODE;
  });
  expect(mode).toBe("worker");

  const mismatches = [];
  for (const entry of CASES) {
    const body = structuredClone(entry.request);
    if (entry.gpx_fixture) {
      body.gpx_xml = fs.readFileSync(path.join(GOLDEN_DIR, entry.gpx_fixture), "utf8");
    }
    const actualRaw = await page.evaluate(async ({ endpoint, request }) => {
      const client = await import("/engine_client.js");
      if (endpoint === "recalibrate") return client.recalibrate(request);
      return client.generatePlan(request);
    }, { endpoint: entry.endpoint, request: body });

    const expectedRaw = JSON.parse(fs.readFileSync(path.join(GOLDEN_DIR, entry.golden), "utf8"));
    const diffs = diffPaths(stripKeys(expectedRaw), stripKeys(actualRaw));
    if (diffs.length) {
      mismatches.push(formatDiff(entry.name, diffs));
    }
  }

  expect(mismatches, mismatches.join("\n\n")).toEqual([]);
});
