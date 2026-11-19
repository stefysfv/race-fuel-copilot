import fs from "node:fs";
import path from "node:path";

import { defineConfig } from "@playwright/test";

// Service-worker fetches stay invisible unless this is set before the browser launches.
process.env.PW_EXPERIMENTAL_SERVICE_WORKER_NETWORK_EVENTS = "1";

function pythonCommand() {
  const windows = path.join(".venv", "Scripts", "python.exe");
  const unix = path.join(".venv", "bin", "python");
  if (process.platform === "win32" && fs.existsSync(windows)) return windows;
  if (fs.existsSync(unix)) return unix;
  return "python";
}

const python = pythonCommand();

export default defineConfig({
  testDir: "tests",
  testMatch: ["offline_race.spec.mjs", "e2e/**/*.spec.mjs"],
  timeout: 180000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8000",
  },
  webServer: {
    command: `${python} -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`,
    url: "http://127.0.0.1:8000",
    reuseExistingServer: true,
    timeout: 30000,
  },
});
