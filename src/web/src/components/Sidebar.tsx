import { ChevronRight, FileText, Play, Plus } from "lucide-react";
import { statusText } from "../api";
import type { Info, Sample, SessionRow } from "../api";

const variants: Record<string, string> = {
  clean: "理想转写",
  colloquial: "重口语",
  asr_noisy: "识别噪声",
};
interface Props {
  sessions: SessionRow[];
  selected: string;
  samples: Sample[];
  sampleId: string;
  replaying: boolean;
  speed: number;
  busy: boolean;
  info: Info | null;
  onNew: () => void;
  onSelect: (id: string) => void;
  onSampleChange: (id: string) => void;
  onSpeedChange: (speed: number) => void;
  onStart: () => void;
}
export function Sidebar({
  sessions,
  selected,
  samples,
  sampleId,
  replaying,
  speed,
  busy,
  info,
  onNew,
  onSelect,
  onSampleChange,
  onSpeedChange,
  onStart,
}: Props) {
  const sample = samples.find((s) => s.id === sampleId);
  return (
    <aside className="sidebar">
      <div className="section-heading">
        <span>当前会话</span>
        <button
          className="icon-button"
          aria-label="新建会话"
          disabled={busy}
          onClick={() => onNew()}
        >
          <Plus size={16} />
        </button>
      </div>
      <div className="session-list">
        {sessions.length ? (
          sessions.map((s) => (
            <button
              key={s.id}
              className={"session-row " + (selected === s.id ? "selected" : "")}
              onClick={() => onSelect(s.id)}
            >
              <FileText size={16} />
              <span>
                <strong>{s.title || "未命名会议"}</strong>
                <small>
                  {statusText[s.status]} · {s.accepted_count} 段文本
                </small>
              </span>
              {selected === s.id && <ChevronRight size={14} />}
            </button>
          ))
        ) : (
          <p className="muted sidebar-empty">新建会话，或从下方样本开始。</p>
        )}
      </div>
      <div className="sample-controls">
        <div className="eyebrow">合成语料 / CORPUS</div>
        <h2>从一场会议开始</h2>
        <p className="muted">对照理想转写、口语表达与识别噪声下的摘要过程。</p>
        <label htmlFor="sample">会议样本</label>
        <select
          id="sample"
          value={sampleId}
          onChange={(e) => onSampleChange(e.target.value)}
          disabled={!samples.length || replaying}
        >
          {samples.map((s) => (
            <option key={s.id} value={s.id}>
              {s.title} · {variants[s.variant]}
            </option>
          ))}
        </select>
        {sample && (
          <div className="sample-meta">
            <span>{variants[sample.variant]}</span>
            <span>
              {sample.characters.toLocaleString()} 字 / {sample.segments} 段
            </span>
          </div>
        )}
        <label htmlFor="speed">回放速度</label>
        <select
          id="speed"
          value={speed}
          onChange={(e) => onSpeedChange(Number(e.target.value))}
        >
          <option value={1}>1× · 约 12 字/秒</option>
          <option value={4}>4× · 快速观察</option>
          <option value={10}>10× · 加速回放</option>
          <option value={0}>立即提交全部片段</option>
        </select>
        <button
          className="button primary full"
          disabled={!sampleId || busy || replaying}
          onClick={() => onStart()}
        >
          <Play size={15} />
          新建并回放
        </button>
        {!samples.length && (
          <p className="muted">未找到语料，请检查服务端语料目录。</p>
        )}
      </div>
      <div className="sidebar-footer">
        <span className="status-dot" />
        仅内存会话
        <p>
          结束后保留 {Math.round((info?.retention_seconds || 300) / 60)}{" "}
          分钟，服务重启即释放。
        </p>
      </div>
    </aside>
  );
}
