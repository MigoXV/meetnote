import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  timeout: 60000,
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:8011",
    viewport: { width: 1440, height: 1100 },
  },
  webServer: {
    command: "poetry -C ../.. run meetnote serve --mock --port 8011",
    url: "http://127.0.0.1:8011/health/live",
    reuseExistingServer: true,
    timeout: 30000,
  },
});
