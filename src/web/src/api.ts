export type Status =
  "running" | "finalizing" | "completed" | "failed" | "cancelled";
export const terminal = (status: Status) =>
  ["completed", "failed", "cancelled"].includes(status);
export const statusText: Record<Status, string> = {
  running: "接收中",
  finalizing: "正在收尾",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已取消",
};
export interface Chunk {
  chunk_id: string;
  text: string;
  speaker: string | null;
  seq: number;
}
export interface Summary {
  id: string;
  summary: string;
  key_points: string[];
  keywords: string[];
  decisions: string[];
  actions: { owner: string; task: string; due_date: string }[];
  open_questions: string[];
  chunk_ids: string[];
}
export interface Minutes {
  title: string;
  markdown: string;
  evidence_trace: string[];
  delivery_warnings: string[];
}
export interface SessionRow {
  id: string;
  title: string;
  status: Status;
  accepted_count: number;
}
export interface Snapshot extends SessionRow {
  processed_count: number;
  remaining_chars: number;
  queue_size: number;
  error: string | null;
  chunks: Chunk[];
  summaries: Summary[];
  minutes: Minutes | null;
  warnings: string[];
  debt: number;
  plan: Record<string, unknown> | null;
  plan_revision: number;
  event_cursor: number;
}
export interface Sample {
  id: string;
  meeting_id: string;
  title: string;
  variant: string;
  segments: number;
  characters: number;
}
export interface Transcript {
  id: string;
  title: string;
  segments: { id: string; speaker: string; text: string }[];
}
export interface RunEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
}
export interface Info {
  provider: string;
  model: string;
  nodes: string[];
  retention_seconds: number;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

const errors: Record<string, string> = {
  session_not_found: "会话已释放或服务已重启，请新建会话。",
  session_limit: "临时会话已达到上限，请释放不再使用的会话。",
  input_queue_full: "输入队列已满，请稍后继续。",
  session_text_limit: "本次会话已达到文本上限。",
  session_not_accepting: "会话已停止接收新文本。",
  chunk_id_conflict: "片段 ID 与已接收内容冲突。",
};

export async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const response = await fetch("/api/v1" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : `请求失败 (${response.status})`;
    throw new ApiError(response.status, errors[detail] || detail);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}
