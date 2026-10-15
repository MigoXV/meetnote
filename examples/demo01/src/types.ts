export type Capability =
  "polish" | "abstract" | "minutes" | "keywords" | "actions";
export type SessionStatus =
  "running" | "finalizing" | "completed" | "failed" | "cancelled";
export const terminal = (status: SessionStatus) =>
  ["completed", "failed", "cancelled"].includes(status);
export const statusText: Record<SessionStatus, string> = {
  running: "接收中",
  finalizing: "正在收尾",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已取消",
};
export interface Info {
  name: string;
  provider: string;
  model: string;
  contract_version: number;
  capabilities?: string[];
  nodes: string[];
  retention_seconds: number;
  persistence: boolean;
}
export interface FulltextResult {
  id: string;
  message?: string;
  polished_text?: string;
  final_abstract?: string;
  minutes?: { markdown: string };
  keywords?: string | null;
  next_actions?: { markdown: string };
}
export interface SessionRow {
  id: string;
  title: string;
  status: SessionStatus;
  accepted_count: number;
}
export interface Chunk {
  chunk_id: string;
  seq: number;
  text: string;
  speaker: string | null;
  timestamp?: string | null;
}
export interface Fact {
  id: string;
  text: string;
  kind: string;
  status: string;
  revision: number;
  owner: string;
  due: string;
  topic: string;
  source_ids: string[];
}
export interface Snapshot extends SessionRow {
  meeting_date: string;
  processed_count: number;
  remaining_chars: number;
  queue_size: number;
  error: string | null;
  chunks: Chunk[];
  facts: Fact[];
  revisions: unknown[];
  sources: unknown[];
  minutes: { markdown: string; coverage?: Record<string, number> } | null;
  minutes_preview: {
    blocks: Record<string, string>;
    covered_chars: number;
  } | null;
  warnings: string[];
  stats: Record<string, unknown>;
  event_cursor: number;
}
export interface Sample {
  id: string;
  title: string;
  variant: string;
  segments: number;
  characters: number;
}
export interface Transcript {
  id: string;
  title: string;
  meeting_date: string;
  segments: { id: string; text: string; speaker: string }[];
}
export interface WireEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
}
export interface RequestLog {
  id: number;
  method: string;
  path: string;
  status: number | string;
  ms: number;
}
export interface Receipt {
  chunk_id: string;
  seq: number;
  duplicate: boolean;
}
