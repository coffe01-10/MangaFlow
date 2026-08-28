import { defineConfig } from "@playwright/test";

const reuseExistingServer = process.env.E2E_REUSE_SERVER === "1";
const python = process.env.MANGAFLOW_PYTHON
  ?? (process.platform === "win32" ? ".venv\\Scripts\\python.exe" : ".venv/bin/python");

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./output/playwright/test-results",
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
    },
    {
      command: "npm run serve:e2e:web",
      url: "http://127.0.0.1:3000",
      reuseExistingServer,
      timeout: 120_000,
    },
  ],
});
