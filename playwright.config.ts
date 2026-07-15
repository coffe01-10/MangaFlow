import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  outputDir: "./output/playwright/test-results",
  fullyParallel: false,
  timeout: 45_000,
  use: {
    baseURL: "http://127.0.0.1:3000",
    channel: "chrome",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "npm run serve:e2e:api",
      url: "http://127.0.0.1:8000/api/v1/health",
      reuseExistingServer: true,
      timeout: 120_000,
    },
    {
      command: "npm run serve:e2e:web",
      url: "http://127.0.0.1:3000",
      reuseExistingServer: process.env.E2E_REUSE_SERVER === "1",
      timeout: 120_000,
    },
  ],
});
