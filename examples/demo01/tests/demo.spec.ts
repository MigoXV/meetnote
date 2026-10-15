import { expect, test, type Page } from "@playwright/test";

const TEXT =
  "项目周会确认本周完成接口联调，由陈宁负责，周五提交测试结果。预算上限为三万元，不包括设备采购。设备方案仍待审批，不能提前下单。会上建议增加一次演练，但尚未批准。王敏负责整理风险清单，暂未确定完成日期。";
const info = {
  name: "MeetNote",
  provider: "responses",
  model: "spark25",
  contract_version: 2,
  capabilities: ["fulltext_generate", "rolling_minutes", "minutes_preview"],
  nodes: ["ingest", "extract"],
  retention_seconds: 300,
  persistence: false,
};
function snapshot(overrides: Record<string, unknown> = {}) {
  return {
    id: "session-test",
    title: "接口测试",
    status: "running",
    meeting_date: "",
    accepted_count: 0,
    processed_count: 0,
    remaining_chars: 0,
    queue_size: 0,
    error: null,
    chunks: [],
    facts: [],
    revisions: [],
    sources: [],
    minutes: null,
    minutes_preview: null,
    warnings: [],
    stats: {},
    event_cursor: 1,
    ...overrides,
  };
}
async function setup(page: Page, old = false) {
  await page.route("**/bridge/**", async (route) => {
    const path = new URL(route.request().url()).pathname.slice(
      "/bridge".length,
    );
    let body: unknown = {};
    if (path.startsWith("/health")) body = { status: "ok" };
    else if (path.endsWith("/info"))
      body = old ? { ...info, capabilities: ["rolling_minutes"] } : info;
    else if (path === "/api/v1/samples")
      body = [
        {
          id: "sample-1",
          title: "样本会议",
          variant: "clean",
          segments: 2,
          characters: TEXT.length,
        },
      ];
    else if (path === "/api/v1/samples/sample-1")
      body = {
        id: "sample-1",
        title: "样本会议",
        meeting_date: "2026-10-15",
        segments: [
          { id: "one", speaker: "陈宁", text: TEXT },
          { id: "two", speaker: "王敏", text: "王敏负责整理风险清单。" },
        ],
      };
    else if (path === "/api/v1/sessions") body = [];
    else if (path === "/openapi.json")
      body = { openapi: "3.1.0", info: { title: "MeetNote" } };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "连接实例", exact: true }).click();
  await expect(page.locator(".connection-state")).toContainText("实例已连接");
}

test("独立连接、全文能力顺序、真实失败提示与恢复", async ({ page }) => {
  await setup(page);
  const panel = page.getByRole("region", { name: "全文生成工作区" });
  let calls = 0;
  await page.route("**/bridge/v1/fulltext/generate", async (route) => {
    calls++;
    const body = route.request().postDataJSON();
    expect(body.pipeline).toEqual([
      "minutes",
      "abstract",
      "keywords",
      "actions",
    ]);
    expect(body.text).toBe(TEXT);
    await route.fulfill({
      json:
        calls === 1
          ? {
              id: body.id,
              message: "模型服务繁忙，请稍后重试。",
              minutes: { markdown: "模型服务繁忙，请稍后重试。" },
            }
          : {
              id: body.id,
              minutes: { markdown: "# 测试纪要\n\n陈宁周五提交测试结果。" },
              final_abstract: "会议确认了接口联调安排。",
              keywords: null,
              next_actions: { markdown: "陈宁负责接口联调。" },
            },
    });
  });
  await panel.getByLabel("完整转写").fill(TEXT);
  await panel.getByRole("button", { name: "上移会议纪要" }).click();
  await panel.getByRole("button", { name: "生成所选内容" }).click();
  await expect(
    panel.getByRole("status").filter({ hasText: "存在失败提示" }),
  ).toBeVisible();
  await expect(panel.getByRole("alert")).toContainText("模型服务繁忙");
  await panel.getByRole("button", { name: "生成所选内容" }).click();
  await expect(panel.locator(".document")).toContainText("测试纪要");
  await panel.getByRole("button", { name: "关键词", exact: true }).click();
  await expect(panel.locator(".document")).toContainText("占位");
  await page.getByRole("button", { name: "实例诊断", exact: true }).click();
  await page.getByRole("button", { name: "健康检查", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "健康检查已返回：ok" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "读取 OpenAPI" }).click();
  await expect(
    page.getByText("OpenAPI 原始响应", { exact: true }),
  ).toBeVisible();
});

test("旧实例明确标注全文不可用，连接失败可恢复", async ({ page }) => {
  await setup(page, true);
  await expect(page.getByText(/当前实例未声明全文生成能力/)).toBeVisible();
  const panel = page.getByRole("region", { name: "全文生成工作区" });
  await panel.getByLabel("完整转写").fill(TEXT);
  await expect(
    panel.getByRole("button", { name: "生成所选内容" }),
  ).toBeDisabled();
  await page.route("**/bridge/health/live", (route) =>
    route.fulfill({ status: 502, json: { detail: "无法连接 MeetNote 实例" } }),
  );
  await page.getByRole("button", { name: "重新连接", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("无法连接");
  await expect(
    page.getByRole("button", { name: "连接实例", exact: true }),
  ).toBeEnabled();
});

test("手工会话、幂等重试、SSE、真实收尾与确认释放", async ({ page }) => {
  await setup(page);
  let state = snapshot();
  let posts = 0;
  let finalized = false;
  let finalizingRead = false;
  const bodies: unknown[] = [];
  await page.route("**/bridge/api/v1/sessions**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const method = route.request().method();
    if (path.endsWith("/events")) {
      return route.fulfill({
        contentType: "text/event-stream",
        body: `id: 1\r\ndata: ${JSON.stringify({ id: 1, type: "session.snapshot", data: state })}\r\n\r\n`,
      });
    }
    if (path.endsWith("/chunks")) {
      bodies.push(route.request().postDataJSON());
      posts++;
      if (posts === 1) return route.abort("failed");
      state = snapshot({
        accepted_count: 1,
        processed_count: 1,
        event_cursor: 2,
        chunks: [
          {
            chunk_id: "stable-id",
            seq: 1,
            text: "陈宁周五完成验收。",
            speaker: "陈宁",
          },
        ],
      });
      return route.fulfill({
        status: 202,
        json: { chunk_id: "stable-id", seq: 1, duplicate: true },
      });
    }
    if (path.endsWith("/finalize")) {
      finalized = true;
      return route.fulfill({
        status: 202,
        json: { id: state.id, status: "finalizing" },
      });
    }
    if (method === "DELETE") return route.fulfill({ status: 204 });
    if (path.endsWith("/sessions"))
      return route.fulfill({
        status: method === "POST" ? 201 : 200,
        json: method === "POST" ? state : [state],
      });
    if (finalized) {
      state = {
        ...state,
        status: finalizingRead ? "completed" : "finalizing",
        event_cursor: 3,
        minutes: finalizingRead
          ? { markdown: "# 完成纪要\n\n陈宁周五完成验收。" }
          : null,
      };
      finalizingRead = true;
    }
    return route.fulfill({ json: state });
  });
  await page.getByRole("button", { name: "流式会话", exact: true }).click();
  const panel = page.getByRole("region", { name: "流式会话工作区" });
  await panel.getByRole("button", { name: "创建会话", exact: true }).click();
  await panel.getByLabel("定稿片段").fill("陈宁周五完成验收。");
  await panel.getByLabel("片段 ID", { exact: true }).fill("stable-id");
  await panel.getByRole("button", { name: "提交片段", exact: true }).click();
  await expect(panel.getByRole("alert")).toBeVisible();
  await expect(panel.getByLabel("定稿片段")).toHaveValue("陈宁周五完成验收。");
  await panel.getByRole("button", { name: "提交片段", exact: true }).click();
  await expect(panel.getByText(/幂等重复，未再次入队/)).toBeVisible();
  expect(bodies[0]).toEqual(bodies[1]);
  await panel.getByRole("button", { name: "结束输入", exact: true }).click();
  await expect(
    panel.getByRole("status").filter({ hasText: "正在收尾" }),
  ).toBeVisible();
  await expect(panel.getByRole("button", { name: "提交片段" })).toBeDisabled();
  await panel.getByRole("button", { name: "刷新快照" }).click();
  await expect(panel.locator(".document")).toContainText("完成纪要");
  await panel.getByRole("button", { name: "事件", exact: false }).click();
  await expect(panel.locator(".event-list")).toContainText("session.snapshot");
  await panel.getByLabel("补发游标").fill("0");
  const reconnect = page.waitForRequest(
    (request) =>
      request.url().endsWith("/events") &&
      request.headers()["last-event-id"] === "0",
  );
  await panel.getByRole("button", { name: "重连并补发" }).click();
  await reconnect;
  await panel.getByRole("button", { name: "释放会话", exact: true }).click();
  await expect(panel.getByRole("button", { name: "确认释放" })).toBeVisible();
  await panel.getByRole("button", { name: "确认释放" }).click();
  await expect(
    panel.getByText("从一段发言开始", { exact: true }),
  ).toBeVisible();
});

test("双主题、窄屏和刷新后无浏览器持久化", async ({ page }) => {
  await setup(page);
  await page.getByLabel("完整转写").fill(TEXT);
  for (const width of [1280, 768, 390, 320]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["abyssus", "vallum"]) {
      await page
        .getByRole("button", {
          name: `切换${theme === "abyssus" ? "苍渊" : "白垣"}主题`,
        })
        .click();
      await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      ).toBe(true);
    }
  }
  expect(
    await page.evaluate(() => ({
      local: localStorage.length,
      session: sessionStorage.length,
    })),
  ).toEqual({ local: 0, session: 0 });
  await page.reload();
  await expect(page.locator(".connection-state")).toContainText("未连接实例");
  await expect(
    page.getByText("先连接一个 MeetNote 实例", { exact: true }),
  ).toBeVisible();
});

test("取消全文请求后恢复操作，延迟响应不覆盖取消状态", async ({ page }) => {
  await setup(page);
  const panel = page.getByRole("region", { name: "全文生成工作区" });
  await page.route("**/bridge/v1/fulltext/generate", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await route
      .fulfill({
        json: { id: "delayed", minutes: { markdown: "过期结果不应显示" } },
      })
      .catch(() => {});
  });
  await panel.getByLabel("完整转写").fill(TEXT);
  await panel.getByRole("button", { name: "生成所选内容" }).click();
  await expect(
    page.getByRole("button", { name: "重新连接", exact: true }),
  ).toBeDisabled();
  await panel.getByRole("button", { name: "取消请求", exact: true }).click();
  await expect(
    panel.getByRole("status").filter({ hasText: "已取消请求" }),
  ).toBeVisible();
  await expect(
    panel.getByRole("button", { name: "生成所选内容" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "重新连接", exact: true }),
  ).toBeEnabled();
  await page.waitForTimeout(1100);
  await expect(panel.getByText("过期结果不应显示")).not.toBeVisible();
});

test("样本逐段回放支持暂停继续，ID 稳定且不会自动收尾", async ({ page }) => {
  await setup(page);
  let state = snapshot();
  const ids: string[] = [];
  let finalized = false;
  await page.route("**/bridge/api/v1/sessions**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/events"))
      return route.fulfill({
        contentType: "text/event-stream",
        body: `data: ${JSON.stringify({ id: 1, type: "session.snapshot", data: state })}\n\n`,
      });
    if (path.endsWith("/chunks")) {
      const body = route.request().postDataJSON();
      ids.push(body.chunk_id);
      state = {
        ...state,
        accepted_count: ids.length,
        event_cursor: ids.length + 1,
      };
      return route.fulfill({
        status: 202,
        json: { chunk_id: body.chunk_id, seq: ids.length, duplicate: false },
      });
    }
    if (path.endsWith("/finalize")) finalized = true;
    return route.fulfill({
      json:
        path.endsWith("/sessions") && route.request().method() === "GET"
          ? [state]
          : state,
    });
  });
  await page.getByRole("button", { name: "流式会话", exact: true }).click();
  const panel = page.getByRole("region", { name: "流式会话工作区" });
  await panel.getByRole("button", { name: "创建会话", exact: true }).click();
  await panel.getByLabel("服务端样本").selectOption("sample-1");
  await panel.getByRole("button", { name: "载入样本" }).click();
  await expect(panel.getByText(/已接受 0 \/ 2 段/)).toBeVisible();
  await panel.getByLabel("片段间隔").selectOption("1500");
  await panel.getByRole("button", { name: "逐段回放", exact: true }).click();
  await expect(panel.getByText(/已接受 1 \/ 2 段/)).toBeVisible();
  await panel.getByRole("button", { name: "暂停回放" }).click();
  await panel.getByLabel("片段间隔").selectOption("0");
  await panel.getByRole("button", { name: "继续回放", exact: true }).click();
  await expect(panel.getByText(/已接受 2 \/ 2 段/)).toBeVisible();
  expect(ids).toHaveLength(2);
  expect(new Set(ids).size).toBe(2);
  expect(finalized).toBe(false);
  await expect(
    panel.getByRole("button", { name: "结束输入", exact: true }),
  ).toBeEnabled();
});

test("键盘主路径与双主题实际文字对比度", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await setup(page);
  await page.keyboard.press("Control+Home");
  await page.getByRole("link", { name: "跳转到工作区" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  const panel = page.getByRole("region", { name: "全文生成工作区" });
  await panel.getByLabel("完整转写").focus();
  await page.keyboard.insertText(TEXT);
  await panel.getByLabel("全文润色", { exact: true }).focus();
  await page.keyboard.press("Space");
  await expect(panel.getByLabel("全文润色", { exact: true })).toBeChecked();
  for (const theme of ["vallum", "abyssus"]) {
    if (theme === "abyssus")
      await page.getByRole("button", { name: "切换苍渊主题" }).click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const ratios = await page.evaluate(() => {
      const rgb = (value: string) => value.match(/[\d.]+/g)!.map(Number);
      const luminance = (value: number[]) => {
        const channels = value.slice(0, 3).map((n) => {
          const c = n / 255;
          return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
        });
        return (
          0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
        );
      };
      return [
        ...document.querySelectorAll(
          ".hint, .eyebrow, .connection-state, nav button, .primary:not(:disabled), label, .topbar",
        ),
      ]
        .filter((element) => element.checkVisibility())
        .map((element) => {
          let current: Element | null = element;
          let background: number[] = [];
          while (current) {
            background = rgb(getComputedStyle(current).backgroundColor);
            if (background.length < 4 || background[3] !== 0) break;
            current = current.parentElement;
          }
          const a = luminance(rgb(getComputedStyle(element).color)),
            b = luminance(background);
          return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        });
    });
    expect(Math.min(...ratios)).toBeGreaterThanOrEqual(4.5);
  }
});
