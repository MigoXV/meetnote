import { defineConfig } from "@playwright/test";

// 禁止失败时自动保存包含会议内容的页面快照。
process.env.PLAYWRIGHT_NO_COPY_PROMPT = "1";

export default defineConfig({
  testDir: "./integration",
  timeout: 900_000,
  workers: 1,
  reporter: "line",
  use: {
    baseURL: "http://127.0.0.1:8012",
    viewport: { width: 1440, height: 1100 },
    screenshot: "off",
    video: "off",
    trace: "off",
  },
});
