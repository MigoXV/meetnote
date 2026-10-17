import { defineConfig } from "@playwright/test";
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";
export default defineConfig({
  testDir: "./tests",
  testMatch: "*.spec.ts",
  workers: 1,
  timeout: 30000,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:5176",
    screenshot: "off",
    video: "off",
    trace: "off",
    viewport: { width: 1440, height: 1000 },
  },
  webServer: {
    command: "pnpm dev",
    url: "http://127.0.0.1:5176",
    reuseExistingServer: !process.env.CI,
    timeout: 30000,
  },
});
