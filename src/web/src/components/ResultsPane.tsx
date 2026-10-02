import { useState } from "react";
import ReactMarkdown from "react-markdown";
import { ChevronRight, FileText, Layers3 } from "lucide-react";
import type { Snapshot } from "../api";

const statusNames: Record<string, string> = {
  confirmed: "已确认",
  proposed: "提议",
  unresolved: "待确认",
  conflicted: "有冲突",
  superseded: "已修订",
  retracted: "已撤销",
  resolved: "已解决",
};
const kindNames: Record<string, string> = {
  fact: "事实",
  proposal: "提议",
  decision: "决定",
  action: "行动",
  constraint: "约束",
  question: "问题",
  risk: "风险",
};
interface Props {
  snapshot: Snapshot | null;
  tab: string;
  setTab: (tab: string) => void;
  focusEvidence: (ids: string[]) => void;
}
export function ResultsPane({ snapshot, tab, setTab, focusEvidence }: Props) {
  const [filter, setFilter] = useState("active");
  const evidence = (ids: string[]) =>
    focusEvidence([
      ...new Set(
        snapshot?.sources
          .filter((s) => ids.includes(s.id))
          .map((s) => s.chunk_id) || [],
      ),
    ]);
  const facts = (snapshot?.facts || []).filter(
    (f) =>
      filter === "all" ||
      (filter === "pending"
        ? ["unresolved", "conflicted"].includes(f.status)
        : !["superseded", "retracted", "resolved"].includes(f.status)),
  );
  return (
    <section className="results-pane" aria-label="摘要结果">
      <div className="tabs" role="tablist" aria-label="结果视图">
        {[
          ["summaries", "事实与阶段摘要"],
          ["minutes", "最终纪要"],
          ["history", "修订记录"],
        ].map(([key, label]) => (
          <button
            role="tab"
            aria-selected={tab === key}
            className={tab === key ? "active" : ""}
            key={key}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="results-scroll" role="tabpanel">
        {tab === "summaries" && (
          <>
            <label>
              显示事项{" "}
              <select
                aria-label="事实筛选"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
              >
                <option value="active">当前事项</option>
                <option value="pending">待确认与冲突</option>
                <option value="all">全部历史</option>
              </select>
            </label>
            {facts.length ? (
              facts.map((f) => (
                <article className="summary" key={f.id}>
                  <div className="summary-index">
                    {f.topic} · {kindNames[f.kind]}
                    <span>
                      {statusNames[f.status]} · v{f.revision}
                    </span>
                  </div>
                  <p className="summary-text">{f.text}</p>
                  {(f.owner || f.due) && (
                    <p>
                      负责人：{f.owner || "待确认"} · 期限：{f.due || "待确认"}
                    </p>
                  )}
                  <button
                    className="text-button"
                    onClick={() => evidence(f.source_ids)}
                  >
                    查看原文依据 <ChevronRight size={13} />
                  </button>
                </article>
              ))
            ) : (
              <div className="empty-state">
                <Layers3 size={32} strokeWidth={1} />
                <h3>事实随原文逐步形成</h3>
                <p>处理后的事实会出现在这里，未决内容会明确标注。</p>
              </div>
            )}
          </>
        )}
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
                事项依据：
                {snapshot.minutes.evidence_trace.map((id) => {
                  const f = snapshot.facts.find((fact) => fact.id === id);
                  return (
                    <button
                      className="text-button"
                      key={id}
                      onClick={() => evidence(f?.source_ids || [])}
                    >
                      {id}
                    </button>
                  );
                })}
              </div>
            </article>
          ) : (
            <div className="empty-state">
              <FileText size={32} strokeWidth={1} />
              <h3>在收尾时生成纪要</h3>
              <p>完整处理原文后，生成简明正文与完整事项清单。</p>
            </div>
          ))}
        {tab === "history" &&
          (snapshot?.revisions.length ? (
            snapshot.revisions.map((r) => (
              <article className="summary" key={`${r.fact_id}:${r.revision}`}>
                <div className="summary-index">
                  {r.fact_id} · v{r.revision}
                </div>
                <p>
                  {statusNames[r.before.status]} → {statusNames[r.after.status]}
                </p>
                <p>{r.reason}</p>
                <p>{r.before.text}</p>
                <p>{r.after.text}</p>
                <button
                  className="text-button"
                  onClick={() => evidence(r.source_ids)}
                >
                  查看修订依据
                </button>
              </article>
            ))
          ) : (
            <div className="empty-state">
              <h3>暂无修订</h3>
              <p>改口、问题关闭和冲突处理会保留在这里。</p>
            </div>
          ))}
      </div>
    </section>
  );
}
