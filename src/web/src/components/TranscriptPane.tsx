import { useLayoutEffect, useRef, useState } from "react";
import type { Snapshot } from "../api";
export function TranscriptPane({
  snapshot,
  onAppend,
  canAppend,
}: {
  snapshot: Snapshot | null;
  onAppend: () => void;
  canAppend: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const follow = useRef(true);
  const [unseen, setUnseen] = useState(0);
  const seen = useRef(0);
  useLayoutEffect(() => {
    const count = snapshot?.chunks.length || 0;
    const node = ref.current;
    if (node && count > seen.current) {
      if (follow.current && window.getSelection()?.isCollapsed !== false)
        node.scrollTop = node.scrollHeight;
      else {
        const timer = window.setTimeout(
          () => setUnseen(count - seen.current),
          0,
        );
        return () => window.clearTimeout(timer);
      }
    }
    seen.current = count;
  }, [snapshot?.chunks.length]);
  function latest() {
    const node = ref.current!;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
    follow.current = true;
    seen.current = snapshot?.chunks.length || 0;
    setUnseen(0);
  }
  return (
    <section className="transcript-pane" aria-label="会议转写">
      <div className="pane-heading">
        <h2>转写原文</h2>
        <span>{snapshot?.chunks.length || 0} 段</span>
      </div>
      <div
        className="reading-scroll transcript-scroll"
        ref={ref}
        onScroll={() => {
          const n = ref.current!;
          follow.current = n.scrollHeight - n.scrollTop - n.clientHeight < 64;
          if (follow.current) {
            seen.current = snapshot?.chunks.length || 0;
            setUnseen(0);
          }
        }}
      >
        {snapshot?.chunks.length ? (
          snapshot.chunks.map((c) => (
            <article
              className="transcript-chunk"
              key={c.chunk_id}
              id={`chunk-${c.chunk_id}`}
            >
              <div className="segment-meta">
                <span className="segment-index">
                  片段 {String(c.seq).padStart(2, "0")}
                </span>
                <span>{c.speaker || "发言人未提供"}</span>
                {c.timestamp && <span>{c.timestamp}</span>}
              </div>
              <p>{c.text}</p>
            </article>
          ))
        ) : (
          <div className="empty-state">
            <h3>等待定稿转写</h3>
            <p>会话已建立。追加一段转写，或从输入来源回放样本。</p>
          </div>
        )}
      </div>
      {unseen > 0 && (
        <button className="new-segments" onClick={latest}>
          有新片段，回到最新
        </button>
      )}
      <div className="append-bar">
        <button className="button" onClick={onAppend} disabled={!canAppend}>
          ＋ 追加转写
        </button>
        <span className="muted">已完成识别、不再实时改写的文本</span>
      </div>
    </section>
  );
}
