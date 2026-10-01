import { FileText, Radio, Send } from "lucide-react";
import type { Snapshot } from "../api";
interface Props {
  snapshot: Snapshot | null;
  selected: string;
  active: boolean;
  text: string;
  busy: boolean;
  setText: (value: string) => void;
  onSend: () => Promise<void>;
}
export function TranscriptPane({
  snapshot,
  selected,
  active,
  text,
  busy,
  setText,
  onSend,
}: Props) {
  return (
    <section className="transcript-pane" aria-label="会议转写">
      <div className="pane-heading">
        <h2>
          <FileText size={16} />
          转写原文
        </h2>
        <span className="muted">定稿片段</span>
      </div>
      <div className="transcript-scroll">
        {snapshot?.chunks.length ? (
          snapshot.chunks.map((chunk) => (
            <article
              className="transcript-chunk"
              id={`chunk-${chunk.chunk_id}`}
              key={chunk.chunk_id}
            >
              <div>
                <span className="speaker">{chunk.speaker || "发言人"}</span>
                <span className="segment-index">
                  {String(chunk.seq).padStart(3, "0")}
                </span>
              </div>
              <p>{chunk.text}</p>
            </article>
          ))
        ) : (
          <div className="empty-state">
            <Radio size={30} strokeWidth={1} />
            <h3>等待第一段发言</h3>
            <p>
              回放一份样本，或在下方输入文本。
              <br />
              原文将按接收顺序保留。
            </p>
          </div>
        )}
      </div>
      <form
        className="input-area"
        onSubmit={(e) => {
          e.preventDefault();
          void onSend();
        }}
      >
        <label htmlFor="text-input">追加定稿文本</label>
        <textarea
          id="text-input"
          placeholder="例如：刚才的时间改一下，周四完成验收，周五再发布。"
          maxLength={16000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={!!selected && !active}
        />
        <div>
          <span className="muted">{text.length} / 16000</span>
          <button
            className="button primary"
            disabled={!text.trim() || busy || (!!selected && !active)}
          >
            <Send size={14} />
            提交
          </button>
        </div>
      </form>
    </section>
  );
}
