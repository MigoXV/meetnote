import type { Info, RunEvent, Snapshot } from "../api";
const names: Record<string, string> = {
  ingest: "原文接收",
  extract: "事实抽取",
  fields: "分工与期限核验",
  reconcile: "修订核对",
  commit: "证据提交",
  chapter: "分区成文",
  render: "清单与纪要",
};
const phases: Record<string, string> = {
  started: "正在执行",
  completed: "执行成功",
  failed: "执行失败",
  degraded: "降级处理",
};
export function Pipeline({
  info,
  snapshot,
  events,
  connection,
}: {
  info: Info | null;
  snapshot: Snapshot | null;
  events: RunEvent[];
  connection: string;
}) {
  return (
    <section className="pipeline">
      <h3>处理链路</h3>
      <p>
        事件订阅：{connection} · 当前会话已接收 {snapshot?.accepted_count || 0}{" "}
        段，已处理 {snapshot?.processed_count || 0} 段。
      </p>
      <p className="muted">
        节点可以重复执行。以下状态来自当前会话各节点的最近事件，不代表整场会议只经过一次。接口未提供事件时间时只显示事件序号。
      </p>
      <div className="node-grid">
        {(info?.nodes || Object.keys(names)).map((name) => {
          const event = [...events]
            .reverse()
            .find((e) => e.data.node === name && e.type.startsWith("node."));
          return (
            <div className="node" key={name}>
              <strong>{names[name] || name}</strong>
              <span>
                {event
                  ? phases[event.type.split(".")[1]] || event.type
                  : "尚无节点事件"}
              </span>
              {event && <small>事件 #{event.id}</small>}
            </div>
          );
        })}
      </div>
      <h3>近期事件 · {events.length} 条</h3>
      <div className="event-list">
        {[...events].reverse().map((e) => (
          <details key={e.id}>
            <summary>
              #{e.id} ·{" "}
              {names[String(e.data.node)] || String(e.data.task || "会话")} ·{" "}
              {phases[e.type.split(".")[1]] || e.type}
            </summary>
            <pre>{JSON.stringify(e.data, null, 2)}</pre>
          </details>
        ))}
        {!events.length && (
          <p>当前没有运行事件。尚未收到节点事件时不展示运行进度。</p>
        )}
      </div>
    </section>
  );
}
