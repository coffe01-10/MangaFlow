import { mkdirSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { defineConfig } from "@playwright/test";

const reuseExistingServer = process.env.E2E_REUSE_SERVER === "1";
const python = process.env.MANGAFLOW_PYTHON
  ?? (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python");
const e2eRunId = process.env.MANGAFLOW_E2E_RUN_ID || crypto.randomUUID().replaceAll("-", "");
const e2eRuntime = process.env.MANGAFLOW_E2E_RUNTIME
  || path.join(os.tmpdir(), `mangaflow-e2e-${e2eRunId}`);
process.env.MANGAFLOW_E2E_RUN_ID = e2eRunId;
process.env.MANGAFLOW_E2E_RUNTIME = e2eRuntime;
mkdirSync(e2eRuntime, { recursive: true });
mkdirSync(path.join("output", "playwright"), { recursive: true });
writeFileSync(
  path.resolve("output", "playwright", "owned-runtime.json"),
  JSON.stringify({ runtime: e2eRuntime, runId: e2eRunId }),
);

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./output/playwright/test-results",
  globalTeardown: "./tests/e2e/global-teardown.ts",
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
      reuseExistingServer,
      timeout: 120_000,
      env: {
        ...process.env,
        MANGAFLOW_DISABLE_DOTENV: "1",
        MANGAFLOW_E2E_SEED: "1",
        MANGAFLOW_E2E_RUN_ID: e2eRunId,
        MANGAFLOW_E2E_RUNTIME: e2eRuntime,
      },
    },
    {
      command: "npm run serve:e2e:web",
      url: "http://127.0.0.1:3000",
      reuseExistingServer,
      timeout: 120_000,
    },
  ],
});
