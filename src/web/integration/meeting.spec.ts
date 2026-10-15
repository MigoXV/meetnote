import { expect, test } from "@playwright/test";

test("真实 4B 主模型完整会议与会话释放", async ({ page, request }) => {
  const info = await (await request.get("/api/v1/info")).json();
  expect(info.provider).toBe("responses");
  expect(info.model).toBe("spark25");
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByText("让讨论，逐渐清晰。")).toBeVisible();
  await page.getByLabel("会议样本").selectOption("product_clean");
  await page.getByLabel("回放速度").selectOption("0");
  await page.getByRole("button", { name: "新建并回放" }).click();
  await expect(page.getByText("会话已结束", { exact: true })).toBeVisible({
    timeout: 850_000,
  });
  const sessions = await (await request.get("/api/v1/sessions")).json();
  expect(sessions).toHaveLength(1);
  const snapshot = await (
    await request.get(`/api/v1/sessions/${sessions[0].id}`)
  ).json();
  expect(snapshot.accepted_count).toBe(24);
  expect(snapshot.processed_count).toBe(24);
  expect(snapshot.remaining_chars).toBe(0);
  expect(snapshot.error).toBeNull();
  expect(snapshot.warnings).toEqual([]);
  expect(snapshot.stats.responses_requests).toBeGreaterThan(0);
  await page.getByRole("tab", { name: "最终纪要" }).click();
  await expect(page.locator(".minutes")).toContainText("会议概述");
  await expect(page.locator(".transcript-chunk")).toHaveCount(24);
  await page.getByRole("button", { name: "取消并释放会话" }).click();
  await expect(page.getByText("让讨论，逐渐清晰。")).toBeVisible();
  expect(await (await request.get("/api/v1/sessions")).json()).toEqual([]);
  expect(errors).toEqual([]);
});
