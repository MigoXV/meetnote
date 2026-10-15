import type { RequestLog, WireEvent } from "./types";

export function normalizeTarget(value: string) {
  const url = new URL(value.trim());
  if (
    !["http:", "https:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      "请输入 HTTP 或 HTTPS 服务根地址，不包含账号、查询参数或片段。",
    );
  }
  return url.toString().replace(/\/$/, "");
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public payload?: unknown,
  ) {
    super(message);
  }
}

export class Client {
  private sequence = 0;
  constructor(
    public target: string,
    private report: (entry: RequestLog) => void,
  ) {}
  async response(
    path: string,
    method: string,
    body?: unknown,
    signal?: AbortSignal,
    headers?: Record<string, string>,
  ) {
    const start = performance.now();
    let status: number | string = "网络错误";
    try {
      const response = await fetch("/bridge" + path, {
        method,
        signal,
        cache: "no-store",
        headers: {
          "X-MeetNote-Target": this.target,
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...headers,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      status = response.status;
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        const detail = payload.detail ?? payload.message;
        throw new ApiError(
          response.status,
          typeof detail === "string"
            ? detail
            : detail === undefined
              ? `请求失败（HTTP ${response.status}），请检查实例和接口版本。`
              : JSON.stringify(detail),
          payload,
        );
      }
      return response;
    } catch (error) {
      if (signal?.aborted) status = "已取消";
      throw error;
    } finally {
      this.report({
        id: ++this.sequence,
        method,
        path,
        status,
        ms: Math.round(performance.now() - start),
      });
    }
  }
  async request<T>(
    path: string,
    method = "GET",
    body?: unknown,
    signal?: AbortSignal,
  ): Promise<T> {
    const response = await this.response(path, method, body, signal);
    if (response.status === 204) return undefined as T;
    try {
      return await response.json();
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new ApiError(
        response.status,
        "实例未返回合法 JSON，请检查服务地址和接口版本。",
      );
    }
  }
  async events(
    id: string,
    cursor: number | null,
    signal: AbortSignal,
    receive: (event: WireEvent) => void,
  ) {
    const response = await this.response(
      `/api/v1/sessions/${encodeURIComponent(id)}/events`,
      "GET",
      undefined,
      signal,
      cursor === null ? undefined : { "Last-Event-ID": String(cursor) },
    );
    if (
      !response.headers.get("content-type")?.includes("text/event-stream") ||
      !response.body
    ) {
      throw new Error("实例未返回 SSE 事件流，请检查服务版本或代理配置。");
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { done, value } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        let boundary: RegExpExecArray | null;
        while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
          const frame = buffer.slice(0, boundary.index);
          buffer = buffer.slice(boundary.index + boundary[0].length);
          const data = frame
            .split(/\r?\n/)
            .filter((line) => line.startsWith("data:"))
            .map((line) => line.slice(5).trimStart())
            .join("\n");
          if (data) {
            const event: WireEvent = JSON.parse(data);
            if (
              !Number.isInteger(event.id) ||
              typeof event.type !== "string" ||
              !event.data
            )
              throw new Error("事件格式不受支持，请查看实例版本。");
            receive(event);
          }
        }
        if (buffer.length > 5 * 1024 * 1024)
          throw new Error("事件超出客户端内存边界，请重新订阅快照。");
        if (done) break;
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }
}

export const errorText = (error: unknown) =>
  error instanceof Error ? error.message : String(error);
export const sessionPath = (id: string) =>
  "/api/v1/sessions/" + encodeURIComponent(id);
