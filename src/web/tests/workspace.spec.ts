import { expect, test } from "@playwright/test";

test("回放、收尾、双主题与窄屏", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByText("让讨论，逐渐清晰。")).toBeVisible();
  await page.screenshot({
    path: "/tmp/meetnote-empty-abyssus.png",
    fullPage: true,
  });
  await page.getByLabel("回放速度").selectOption("0");
  await page.getByRole("button", { name: "新建并回放" }).click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await expect(page.locator(".summary")).not.toHaveCount(0);
  await page.getByRole("tab", { name: "最终纪要" }).click();
  await expect(page.locator(".minutes")).toContainText("完整事项清单");
  await page.screenshot({
    path: "/tmp/meetnote-result-abyssus.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "切换白垣主题" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "vallum");
  await page.waitForTimeout(250);
  await page.screenshot({
    path: "/tmp/meetnote-result-vallum.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator("body")).toBeVisible();
  const overflow = await page.evaluate(
    () => document.documentElement.scrollWidth > window.innerWidth,
  );
  expect(overflow).toBe(false);
  await page.screenshot({ path: "/tmp/meetnote-mobile.png", fullPage: true });
  await page.getByRole("button", { name: "取消并释放会话" }).click();
  await expect(page.getByText("让讨论，逐渐清晰。")).toBeVisible();
  expect(errors).toEqual([]);
});

test("手工输入和暂停继续不重复提交", async ({ page }) => {
  await page.goto("/");
  await page
    .getByLabel("追加定稿文本")
    .fill("本次会议确认周五验收，陈宁负责检查，预算上限十二万元。");
  await page.getByRole("button", { name: "提交", exact: true }).click();
  await expect(page.locator(".transcript-chunk")).toHaveCount(1);
  await page.getByRole("button", { name: "收尾", exact: true }).click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "取消并释放会话" }).click();
  await page.getByLabel("回放速度").selectOption("1");
  await page.getByRole("button", { name: "新建并回放" }).click();
  await expect(page.getByRole("button", { name: "暂停回放" })).toBeVisible();
  await page.getByRole("button", { name: "暂停回放" }).click();
  const count = await page.locator(".transcript-chunk").count();
  await page.getByLabel("回放速度").selectOption("0");
  await page.getByRole("button", { name: "继续回放" }).click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible();
  const segments = await page.locator(".transcript-chunk").count();
  expect(segments).toBeGreaterThan(count);
  const labels = await page.locator(".segment-index").allTextContents();
  expect(new Set(labels).size).toBe(labels.length);
  await page.getByRole("button", { name: "取消并释放会话" }).click();
});
