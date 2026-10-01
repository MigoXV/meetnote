import ReactMarkdown from "react-markdown";
import { Activity, Check, ChevronRight, FileText, Layers3 } from "lucide-react";
import type { Snapshot } from "../api";

interface Props {
  snapshot: Snapshot | null;
  tab: string;
  setTab: (tab: string) => void;
  focusEvidence: (ids: string[]) => void;
}

export function ResultsPane({ snapshot, tab, setTab, focusEvidence }: Props) {
  return (
    <section className="results-pane" aria-label="摘要结果">
      <div className="tabs" role="tablist" aria-label="结果视图">
        {[
          ["summaries", "实时摘要"],
          ["minutes", "最终纪要"],
          ["plan", "规划"],
        ].map(([key, label]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            key={key}
            onClick={() => setTab(key)}
          >
            {label}
            {key === "summaries" && (
              <span className="count">{snapshot?.summaries.length || 0}</span>
            )}
          </button>
        ))}
      </div>
      <div className="results-scroll" role="tabpanel">
        {tab === "summaries" &&
          (snapshot?.summaries.length ? (
            snapshot.summaries.map((summary, i) => (
              <article className="summary" key={summary.id}>
                <div className="summary-index">
                  摘要 {String(i + 1).padStart(2, "0")}
                  <span>
                    <Check size={12} />
                    已提交
                  </span>
                </div>
                <p className="summary-text">{summary.summary}</p>
                {!!summary.key_points.length && (
                  <ul>
                    {summary.key_points.map((point, n) => (
                      <li key={n}>{point}</li>
                    ))}
                  </ul>
                )}
                {!!summary.decisions.length && (
                  <div className="subsection">
                    <strong>决定</strong>
                    <ul>
                      {summary.decisions.map((d, n) => (
                        <li key={n}>{d}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {!!summary.actions.length && (
                  <div className="subsection">
                    <strong>行动项</strong>
                    {summary.actions.map((a, n) => (
                      <p key={n}>
                        {a.owner || "负责人待确认"} · {a.task}{" "}
                        {a.due_date && ` / ${a.due_date}`}
                      </p>
                    ))}
                  </div>
                )}
                {!!summary.open_questions.length && (
                  <div className="subsection">
                    <strong>待确认</strong>
                    <ul>
                      {summary.open_questions.map((q, n) => (
                        <li key={n}>{q}</li>
                      ))}
                    </ul>
                  </div>
                )}
                <button
                  className="text-button"
                  onClick={() => focusEvidence(summary.chunk_ids)}
                >
                  查看 {summary.chunk_ids.length} 段原文依据{" "}
                  <ChevronRight size={13} />
                </button>
              </article>
            ))
          ) : (
            <div className="empty-state">
              <Layers3 size={32} strokeWidth={1} />
              <h3>为完整的意思留一点时间</h3>
              <p>
                信号与边界确认后，摘要会在这里出现。
                <br />
                尚未闭合的内容留待下一轮处理。
              </p>
            </div>
          ))}
        {tab === "minutes" &&
          (snapshot?.minutes ? (
            <article className="minutes">
              <ReactMarkdown skipHtml>
                {snapshot.minutes.markdown}
              </ReactMarkdown>
              {!!snapshot.minutes.delivery_warnings.length && (
                <aside className="notice">
                  <strong>交付说明</strong>
                  <ul>
                    {snapshot.minutes.delivery_warnings.map((w, i) => (
                      <li key={i}>{w}</li>
                    ))}
                  </ul>
                </aside>
              )}
              <div className="evidence-footer">
                依据：
                {snapshot.minutes.evidence_trace.join("、") || "参见实时摘要"}
              </div>
            </article>
          ) : (
            <div className="empty-state">
              <FileText size={32} strokeWidth={1} />
              <h3>在收尾时生成纪要</h3>
              <p>
                点击「收尾」停止输入，待队列处理完成后，
                <br />
                引擎会整理整场会议的证据与规划。
              </p>
            </div>
          ))}
        {tab === "plan" &&
          (snapshot?.plan ? (
            <div className="plan-view">
              <div className="eyebrow">规划修订 {snapshot.plan_revision}</div>
              {Object.entries(snapshot.plan).map(([key, value]) => (
                <div key={key}>
                  <h3>
                    {(
                      {
                        body_axis: "组织主轴",
                        section_order: "章节安排",
                        key_topics: "关键议题",
                        content_promotion_policy: "结论采纳规则",
                        evidence_policy: "证据规则",
                        composer_brief: "写作简报",
                        uncertainty_policy: "不确定性处理",
                      } as Record<string, string>
                    )[key] || key}
                  </h3>
                  <p>
                    {Array.isArray(value) ? value.join(" · ") : String(value)}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-state">
              <Activity size={32} strokeWidth={1} />
              <h3>规划随证据形成</h3>
              <p>累积摘要后，会议方向与章节提示将逐步更新。</p>
            </div>
          ))}
      </div>
    </section>
  );
}
