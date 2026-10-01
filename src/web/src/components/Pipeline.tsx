import { Activity, ArrowDown, Check, Circle, X } from "lucide-react";
import type { Info, RunEvent, Snapshot } from "../api";

const nodeNames: Record<string, string> = {
  bucket_accumulator: "原文接收",
  cycle_slice_builder: "检查点切片",
  fallback_guard: "可用性检查",
  llm_signal_marker: "语义信号",
  commit_debt_meter: "债务累计",
  work_candidate_builder: "候选任务",
  model_work_scheduler: "模型调度",
  meeting_lens_builder: "会议方向",
  template_planner: "结构提示",
  summary_worker: "局部摘要",
  bucket_enricher: "证据整理",
  main_minutes_planner: "纪要规划",
  minutes_composer: "纪要生成",
};

interface Props {
  info: Info | null;
  snapshot: Snapshot | null;
  events: RunEvent[];
  connection: string;
}

export function Pipeline({ info, snapshot, events, connection }: Props) {
  const nodeState = (name: string) =>
    [...events].reverse().find((event) => String(event.data.node) === name);
  return (
    <section className="pipeline">
      <div className="pipeline-heading">
        <h2>
          <Activity size={16} />
          处理链路
        </h2>
        <span>{connection}</span>
        <span className="pipeline-stats">
          债务 {snapshot?.debt.toFixed(1) || "0.0"} / 3 · 待处理{" "}
          {snapshot?.remaining_chars || 0} 字
        </span>
      </div>
      <div className="node-grid">
        {(info?.nodes || Object.keys(nodeNames)).map((name, i) => {
          const event = nodeState(name);
          const phase = event?.type.split(".")[1] || "idle";
          return (
            <div className={`node ${phase}`} key={name}>
              <span className="node-number">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span>{nodeNames[name]}</span>
              {phase === "started" ? (
                <Activity size={12} />
              ) : phase === "failed" ? (
                <X size={12} />
              ) : phase === "degraded" ? (
                <span>!</span>
              ) : phase === "completed" ? (
                <Check size={12} />
              ) : (
                <Circle size={8} />
              )}
            </div>
          );
        })}
      </div>
      <details className="event-details">
        <summary>
          运行事件 <span>{events.length} 条最近事件</span>
          <ArrowDown size={13} />
        </summary>
        <div className="event-list">
          {events.length ? (
            [...events].reverse().map((e) => (
              <details key={e.id}>
                <summary>
                  <span className="event-id">{e.id}</span>
                  <code>{e.type}</code>
                  <span>{String(e.data.node || e.data.task || "")}</span>
                </summary>
                <pre>{JSON.stringify(e.data, null, 2)}</pre>
              </details>
            ))
          ) : (
            <p className="muted">尚无运行事件。</p>
          )}
        </div>
      </details>
    </section>
  );
}
