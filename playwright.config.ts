import { defineConfig } from "@playwright/test";
import path from "node:path";
import { assertSupervised, defaultPython } from "./scripts/phase2_runner.mjs";

const owned = assertSupervised();
const python = defaultPython(process.cwd());
if (process.env.E2E_REUSE_SERVER === "1") {
  throw new Error("Acceptance cannot reuse an unknown server");
}

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: path.join("./output/playwright", owned.runId, "test-results"),
  globalSetup: "./tests/e2e/global-setup.ts",
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  use: {
    baseURL: "http://127.0.0.1:3000",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: `"${python}" scripts/serve_e2e_api.py`,
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: false,
      timeout: 120_000,
    },
    {
      command: `"${process.execPath}" node_modules/next/dist/bin/next start apps/web --hostname 127.0.0.1`,
      url: "http://127.0.0.1:3000",
      reuseExistingServer: false,
      timeout: 120_000,
    },
  ],
});
