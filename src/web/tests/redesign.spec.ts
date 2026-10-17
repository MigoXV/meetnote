import { expect, test } from "@playwright/test";
import type { Snapshot } from "../src/api";
import { demoSnapshot, stressSnapshot } from "./fixtures";
async function mount(page: import("@playwright/test").Page, state: Snapshot) {
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/info"))
      return route.fulfill({
        json: {
          provider: "mock",
          model: "test",
          nodes: [
            "ingest",
            "extract",
            "fields",
            "reconcile",
            "commit",
            "chapter",
            "render",
          ],
          retention_seconds: 300,
        },
      });
    if (path.endsWith("/samples")) return route.fulfill({ json: [] });
    if (path.endsWith("/events"))
      return route.fulfill({
        contentType: "text/event-stream",
        body: ": fixture only\n\n",
      });
    if (path.endsWith("/sessions"))
      return route.fulfill({
        json: [
          {
            id: state.id,
            title: state.title,
            status: state.status,
            accepted_count: state.accepted_count,
          },
        ],
      });
    return route.fulfill({ json: state });
  });
  await page.goto("/");
  await page
    .getByRole("button", {
      name: new RegExp(state.title.replace(/[()]/g, ".")),
    })
    .click();
  await expect(page.locator("h1")).toContainText(state.title);
}
test("连贯演示：类型、未知字段、依据返回、修订及保存", async ({ page }) => {
  const state = demoSnapshot();
  await page.setViewportSize({ width: 1440, height: 900 });
  await mount(page, state);
  await page.screenshot({ path: "/tmp/meetnote-v2-browser-desktop.png" });
  await expect(page.getByRole("heading", { name: "行动项 3" })).toBeVisible();
  await expect(page.getByText("未指定", { exact: true })).toBeVisible();
  await expect(page.getByText("未明确", { exact: true })).toBeVisible();
  await page
    .locator(".fact-row")
    .filter({ hasText: "完成转写页" })
    .getByRole("button", { name: "查看依据" })
    .click();
  await expect(page.getByRole("dialog")).toContainText("片段 03");
  await expect(page.getByRole("dialog")).toContainText("片段 04");
  await page.getByRole("button", { name: "返回此前事项" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page
      .locator(".fact-row")
      .filter({ hasText: "完成转写页" })
      .getByRole("button"),
  ).toBeFocused();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: "/tmp/meetnote-v2-browser-mobile.png" });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.getByRole("tab", { name: "修订记录" }).click();
  await expect(page.locator(".revision-values")).toContainText(
    "周五完成验收，下周一发布",
  );
  await expect(page.locator(".revision-values")).toContainText(
    "周四验收，周五发布",
  );
  await page.getByRole("tab", { name: "会议纪要" }).click();
  await expect(page.locator(".minutes")).toContainText("尚未收尾");
  await page.getByRole("button", { name: "复制 / 保存" }).first().click();
  await expect(page.getByRole("dialog")).toContainText("不包含");
});
test("长文、数十事项、窄屏和双主题不溢出；纪要连续阅读", async ({ page }) => {
  const state = stressSnapshot();
  expect(state.chunks.reduce((n, c) => n + c.text.length, 0)).toBeGreaterThan(
    40000,
  );
  await mount(page, state);
  await expect(page.locator(".transcript-chunk")).toHaveCount(160);
  await expect(page.locator(".fact-row")).toHaveCount(64);
  for (const [width, height] of [
    [1440, 900],
    [1366, 768],
    [1024, 768],
    [390, 844],
    [320, 844],
  ]) {
    await page.setViewportSize({ width, height });
    await expect
      .poll(() =>
        page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
      )
      .toBe(true);
    const readable = await page
      .locator(".results-scroll")
      .evaluate((e) => e.clientHeight);
    expect(readable).toBeGreaterThan(300);
    if (width <= 390) {
      await page.getByRole("tab", { name: "原文", exact: true }).click();
      await expect(page.locator(".transcript-scroll")).toBeVisible();
      await page.getByRole("tab", { name: "概览", exact: true }).click();
    }
  }
  await page.getByRole("button", { name: "切换苍渊主题" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "abyssus");
  await page.screenshot({ path: "/tmp/meetnote-v2-stress-dark.png" });
});
test("超限与中文输入法不误提交；失败保留内容并幂等重试", async ({ page }) => {
  await page.route("**/api/v1/info", (r) =>
    r.fulfill({
      json: {
        provider: "mock",
        model: "test",
        nodes: [],
        retention_seconds: 300,
      },
    }),
  );
  await page.route("**/api/v1/samples", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/v1/sessions", (r) =>
    r.fulfill({ json: r.request().method() === "POST" ? demoSnapshot() : [] }),
  );
  await page.route("**/api/v1/sessions/demo", (r) =>
    r.fulfill({ json: demoSnapshot() }),
  );
  const requests: unknown[] = [];
  await page.route("**/api/v1/sessions/demo/chunks", async (r) => {
    requests.push(r.request().postDataJSON());
    if (requests.length === 1)
      return r.fulfill({ status: 503, json: { detail: "服务暂时不可用" } });
    return r.fulfill({
      json: { chunk_id: "receipt", seq: 11, duplicate: true },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "粘贴转写文本" }).click();
  const input = page.getByLabel("转写文本", { exact: true });
  await input.fill("😀".repeat(16001));
  await expect(page.getByRole("dialog")).toContainText("内容超限");
  await expect(
    page
      .getByRole("dialog")
      .getByRole("button", { name: "追加转写", exact: true }),
  ).toBeDisabled();
  await input.fill("周四验收。");
  await input.dispatchEvent("keydown", {
    key: "Enter",
    code: "Enter",
    ctrlKey: true,
    isComposing: true,
  });
  expect(requests).toHaveLength(0);
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "追加转写", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toContainText("输入已保留");
  await expect(input).toHaveValue("周四验收。");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "追加转写", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(requests).toHaveLength(2);
  expect(requests[0]).toEqual(requests[1]);
});
test("筛选为空与局部失败保留已有原文和结果", async ({ page }) => {
  const s = demoSnapshot();
  s.facts = s.facts.filter((f) => f.status === "confirmed");
  s.status = "failed";
  s.error = "纪要生成失败（演示状态）";
  await mount(page, s);
  await page.getByRole("button", { name: "待确认 0", exact: true }).click();
  await expect(page.getByText("此筛选下无匹配事项")).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.locator(".fact-row")).toHaveCount(4);
  await expect(page.locator(".transcript-chunk")).toHaveCount(10);
  await expect(
    page.getByRole("alert").filter({ hasText: "处理未完成" }),
  ).toBeVisible();
});

test("会话过期保留当前页面成果；断连不清空原文", async ({ page }) => {
  const s = demoSnapshot();
  await mount(page, s);
  await expect(page.locator(".transcript-chunk")).toHaveCount(10);
  await page.route("**/api/v1/sessions/demo", (r) =>
    r.fulfill({ status: 404, json: { detail: "session_not_found" } }),
  );
  await expect(
    page.getByText(
      "会话已释放或服务已重启。以下仅为当前页面仍持有的内容，无法继续处理；请立即保存已有纪要。",
    ),
  ).toBeVisible({ timeout: 6000 });
  await expect(page.locator(".transcript-chunk")).toHaveCount(10);
  await expect(page.locator(".fact-row")).toHaveCount(7);
  await expect(
    page.getByRole("button", { name: "收尾并生成纪要" }).first(),
  ).toBeDisabled();
});

test("跟随最新只在底部生效；证据返回和刷新不抢输入焦点", async ({ page }) => {
  const state = stressSnapshot();
  await page.setViewportSize({ width: 1440, height: 900 });
  await mount(page, state);
  const stream = page.locator(".transcript-scroll");
  await stream.evaluate((e) => {
    e.scrollTop = 0;
  });
  await page.waitForTimeout(100);
  state.chunks.push({
    chunk_id: "last",
    seq: 161,
    text: "新增片段仅用于跟随行为测试。",
    speaker: null,
  });
  state.accepted_count = 161;
  await expect(
    page.getByRole("button", { name: "有新片段，回到最新" }),
  ).toBeVisible({ timeout: 6000 });
  expect(await stream.evaluate((e) => e.scrollTop)).toBeLessThan(100);
  await page.getByRole("button", { name: "有新片段，回到最新" }).click();
  await expect
    .poll(() =>
      stream.evaluate((e) => e.scrollHeight - e.scrollTop - e.clientHeight),
    )
    .toBeLessThan(64);
  await page
    .getByRole("button", { name: "追加转写", exact: true })
    .first()
    .click();
  await page
    .getByLabel("转写文本", { exact: true })
    .fill("正在编辑，不应丢失焦点");
  await page.waitForTimeout(2200);
  await expect(page.getByLabel("转写文本", { exact: true })).toBeFocused();
  await expect(page.getByLabel("转写文本", { exact: true })).toHaveValue(
    "正在编辑，不应丢失焦点",
  );
});

test("中文长名称与 200% 字体、键盘分隔、下载与可见焦点", async ({ page }) => {
  const s = demoSnapshot();
  s.title =
    "栖叶协作产品周会 / CrossFunctionalMeetingEvidenceReview-LongUnbrokenIdentifierAnd中文长名称";
  s.status = "completed";
  await mount(page, s);
  await page.locator('[role="separator"]').focus();
  const old = await page
    .locator('[role="separator"]')
    .getAttribute("aria-valuenow");
  await page.keyboard.press("ArrowRight");
  expect(
    await page.locator('[role="separator"]').getAttribute("aria-valuenow"),
  ).not.toBe(old);
  await page.getByRole("tab", { name: "会议纪要" }).click();
  await page.getByRole("button", { name: "复制 / 保存" }).first().click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 Markdown" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.md$/);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "复制 / 保存" }).first(),
  ).toBeFocused();
  await page.addStyleTag({ content: "body{font-size:30px;line-height:1.7}" });
  await page.setViewportSize({ width: 320, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("实际配色对比、移动操作命中和焦点可见", async ({ page }) => {
  await mount(page, demoSnapshot());
  for (const theme of ["vallum", "abyssus"]) {
    if (theme === "abyssus")
      await page.getByRole("button", { name: "切换苍渊主题" }).click();
    const violations = await page.evaluate(() => {
      const rgb = (s: string) => s.match(/[\d.]+/g)!.map(Number);
      const lum = (s: number[]) =>
        s
          .slice(0, 3)
          .map((v) => v / 255)
          .map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4))
          .reduce((a, v, i) => a + v * [0.2126, 0.7152, 0.0722][i], 0);
      const ratio = (a: number[], b: number[]) => {
        const l = [lum(a), lum(b)].sort((x, y) => y - x);
        return (l[0] + 0.05) / (l[1] + 0.05);
      };
      const result: { text: string; ratio: number }[] = [];
      for (const el of document.querySelectorAll(
        "p,h1,h2,h3,button,dt,dd,summary,select",
      )) {
        const n = el as HTMLElement;
        const rect = n.getBoundingClientRect();
        if (
          !rect.width ||
          !rect.height ||
          n.closest("button:disabled") ||
          !n.textContent?.trim()
        )
          continue;
        const style = getComputedStyle(n);
        let ancestor: HTMLElement | null = n;
        let bg: number[] = [248, 247, 242];
        while (ancestor) {
          const value = rgb(getComputedStyle(ancestor).backgroundColor);
          if ((value[3] ?? 1) > 0.99) {
            bg = value;
            break;
          }
          ancestor = ancestor.parentElement;
        }
        const contrast = ratio(rgb(style.color), bg);
        const large =
          parseFloat(style.fontSize) >= 24 ||
          (parseFloat(style.fontSize) >= 18.66 &&
            parseInt(style.fontWeight) >= 700);
        if (contrast < (large ? 3 : 4.5))
          result.push({
            text: n.textContent.trim().slice(0, 50),
            ratio: contrast,
          });
      }
      return result;
    });
    expect(violations).toEqual([]);
  }
  await page.setViewportSize({ width: 390, height: 844 });
  const small = await page
    .locator("button:visible:not(:disabled)")
    .evaluateAll((buttons) =>
      buttons
        .map((b) => ({
          name: b.getAttribute("aria-label") || b.textContent?.trim(),
          w: b.getBoundingClientRect().width,
          h: b.getBoundingClientRect().height,
        }))
        .filter((b) => b.w < 44 || b.h < 44),
    );
  expect(small).toEqual([]);
  await page.getByRole("button", { name: "切换白垣主题" }).focus();
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  expect(
    await page
      .getByRole("button", { name: "切换白垣主题" })
      .evaluate((e) => getComputedStyle(e).outlineStyle),
  ).not.toBe("none");
});
