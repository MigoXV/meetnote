import { Plus, PanelLeftClose } from "lucide-react";
import { statusText } from "../api";
import type { SessionRow } from "../api";
export function Sidebar({
  sessions,
  selected,
  busy,
  onNew,
  onSelect,
  onClose,
}: {
  sessions: SessionRow[];
  selected: string;
  busy: boolean;
  onNew: () => void;
  onSelect: (id: string) => void;
  onClose: () => void;
}) {
  return (
    <nav className="sidebar" aria-label="临时会话">
      <div className="section-heading">
        <span>临时会话</span>
        <button
          className="icon-button"
          aria-label="收起会话导航"
          onClick={onClose}
        >
          <PanelLeftClose size={18} />
        </button>
      </div>
      <button className="button" disabled={busy} onClick={onNew}>
        <Plus size={16} />
        新建会话
      </button>
      <div className="session-list">
        {sessions.length ? (
          sessions.map((s) => (
            <button
              className={`session-row ${selected === s.id ? "selected" : ""}`}
              aria-current={selected === s.id ? "true" : undefined}
              onClick={() => onSelect(s.id)}
              key={s.id}
            >
              <strong>{s.title || "未命名会议"}</strong>
              <span>
                {statusText[s.status]} · {s.accepted_count} 段
              </span>
            </button>
          ))
        ) : (
          <p className="muted">当前没有可访问的会话。</p>
        )}
      </div>
      <p className="sidebar-footer">
        仅服务内存
        <br />
        服务重启即释放
        <br />
        结束后按服务规则短暂保留。
      </p>
    </nav>
  );
}
