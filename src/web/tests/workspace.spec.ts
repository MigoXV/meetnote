import { expect, test } from "@playwright/test";
async function release(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "取消并释放会话" }).click();
  await page.getByRole("button", { name: "确认释放会话" }).click();
  await expect(
    page.getByRole("button", { name: "粘贴转写文本" }),
  ).toBeVisible();
}
test("真实 mock 消费者：回放、自动收尾、双主题与释放", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "会议摘要工作台" }),
  ).toBeVisible();
  await page.getByLabel("回放速度").selectOption("0");
  await page.getByRole("button", { name: "新建并回放" }).click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await page.getByRole("tab", { name: "会议纪要" }).click();
  await expect(page.locator(".minutes")).toContainText("纪要依据");
  // 长语料可能触发现有 mock 成文校验失败；界面必须保留已有内容并如实标明。
  await expect(page.locator(".transcript-chunk")).not.toHaveCount(0);
  await page.getByRole("button", { name: "切换苍渊主题" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "abyssus");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
  ).toBe(false);
  await release(page);
  expect(errors).toEqual([]);
});
test("真实 mock 消费者：手工追加及暂停继续不重复提交", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "粘贴转写文本" }).click();
  await page
    .getByLabel("转写文本", { exact: true })
    .fill("本次会议确认周五验收，陈宁负责检查，预算上限十二万元。");
  await page.getByRole("button", { name: "追加转写", exact: true }).click();
  await expect(page.locator(".transcript-chunk")).toHaveCount(1);
  await page
    .getByRole("button", { name: "收尾并生成纪要", exact: true })
    .first()
    .click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "最终纪要", exact: true }),
  ).toBeVisible();
  await release(page);
  await page.getByLabel("回放速度").selectOption("1");
  await page.getByRole("button", { name: "新建并回放" }).click();
  await page.getByRole("button", { name: "暂停回放" }).click();
  const before = await page.locator(".transcript-chunk").count();
  await page.getByRole("button", { name: "输入来源", exact: true }).click();
  await page.getByLabel("回放速度").selectOption("0");
  await page.getByRole("button", { name: "关闭面板" }).click();
  await page.getByRole("button", { name: "继续回放" }).click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible();
  expect(await page.locator(".transcript-chunk").count()).toBeGreaterThan(
    before,
  );
  const ids = await page.locator(".segment-index").allTextContents();
  expect(new Set(ids).size).toBe(ids.length);
  await release(page);
});
