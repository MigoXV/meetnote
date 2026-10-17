import { useState } from "react";
import ReactMarkdown from "react-markdown";
import type { Fact, Snapshot } from "../api";
import { currentFacts, pendingFacts, kindNames, factStatus } from "../model";
export function ResultsPane({
  snapshot,
  tab,
  focusEvidence,
  onFinish,
  onExport,
}: {
  snapshot: Snapshot | null;
  tab: string;
  focusEvidence: (ids: string[], label?: string) => void;
  onFinish: () => void;
  onExport: () => void;
}) {
  const [filter, setFilter] = useState("active");
  const all = snapshot?.facts || [];
  const current = currentFacts(all);
  const pending = pendingFacts(current);
  const facts =
    filter === "all" ? all : filter === "pending" ? pending : current;
  const evidence = (f: Fact) => focusEvidence(f.source_ids, f.text);
  if (tab === "history")
    return (
      <section className="results-pane">
        <div className="reading-scroll">
          <div className="document">
            <h2>修订记录</h2>
            <p className="muted">
              保留原值与当前值。记录以修订号排序；接口未提供发生时间或独立的变更来源类型。
            </p>
            {snapshot?.revisions.length ? (
              snapshot.revisions.map((r) => (
                <article
                  className="revision-row"
                  key={`${r.fact_id}:${r.revision}`}
                >
                  <div className="section-heading">
                    <h3>{r.after.subject || r.fact_id}</h3>
                    <span>修订 {r.revision}</span>
                  </div>
                  <p>{r.reason}</p>
                  <div className="revision-values">
                    <div>
                      <h4>原值 · {factStatus[r.before.status]}</h4>
                      <p>{r.before.text}</p>
                      <p>
                        负责人：{r.before.owner || "未指定"} · 期限：
                        {r.before.due || "未明确"}
                      </p>
                      <button
                        className="text-button"
                        onClick={() =>
                          focusEvidence(r.before.source_ids, "原安排")
                        }
                      >
                        查看原安排依据
                      </button>
                    </div>
                    <div>
                      <h4>新值 · {factStatus[r.after.status]}</h4>
                      <p>{r.after.text}</p>
                      <p>
                        负责人：{r.after.owner || "未指定"} · 期限：
                        {r.after.due || "未明确"}
                      </p>
                      <button
                        className="text-button"
                        onClick={() =>
                          focusEvidence(
                            [
                              ...new Set([
                                ...r.source_ids,
                                ...r.after.source_ids,
                              ]),
                            ],
                            "修订与确认",
                          )
                        }
                      >
                        查看修订与确认依据
                      </button>
                    </div>
                  </div>
                </article>
              ))
            ) : (
              <div className="empty-state">
                <h3>尚无修订记录</h3>
                <p>明确改口、撤销或问题关闭形成修订后，会保留前后值与依据。</p>
              </div>
            )}
          </div>
        </div>
      </section>
    );
  if (tab === "minutes")
    return (
      <section className="results-pane">
        <div className="pane-heading">
          <h2>{snapshot?.status === "completed" ? "最终纪要" : "会议纪要"}</h2>
          {snapshot?.minutes && (
            <button className="button" onClick={onExport}>
              复制 / 保存
            </button>
          )}
        </div>
        <div className="reading-scroll">
          <article className="document minutes">
            {snapshot?.status !== "completed" && (
              <p className="notice">
                {snapshot?.status === "finalizing"
                  ? "正在收尾并生成最终纪要。"
                  : snapshot?.status === "failed"
                    ? "处理未完成。以下为已有内容，不是完整最终结果。"
                    : "尚未收尾。阶段纪要会随处理更新，覆盖范围以服务端结果为准。"}
              </p>
            )}
            {snapshot?.minutes ? (
              <>
                <ReactMarkdown skipHtml>
                  {snapshot.minutes.markdown}
                </ReactMarkdown>
                {snapshot.minutes.delivery_warnings.map((w, i) => (
                  <p className="notice" key={i}>
                    {w}
                  </p>
                ))}
                <div className="evidence-footer">
                  <h3>纪要依据</h3>
                  {snapshot.minutes.evidence_trace.map((id) => {
                    const f = all.find((f) => f.id === id);
                    return (
                      <button
                        className="text-button"
                        key={id}
                        onClick={() =>
                          focusEvidence(f?.source_ids || [], f?.text || id)
                        }
                      >
                        {f?.subject || id} · 查看依据
                      </button>
                    );
                  })}
                </div>
              </>
            ) : (
              <div className="empty-state">
                <h3>
                  {snapshot?.status === "finalizing"
                    ? "正在生成纪要"
                    : "收尾后获得连续阅读的纪要"}
                </h3>
                <p>
                  当前原文与已形成事项会保留。服务完成收尾后显示完整正文与事项清单。
                </p>
                {snapshot?.status === "running" && (
                  <button className="button primary" onClick={onFinish}>
                    收尾并生成纪要
                  </button>
                )}
              </div>
            )}
          </article>
        </div>
      </section>
    );
  return (
    <section className="results-pane">
      <div className="reading-scroll results-scroll">
        <div className="overview-document">
          <section className="stage-summary">
            <h2>阶段摘要</h2>
            {snapshot?.minutes_preview &&
            Object.keys(snapshot.minutes_preview.blocks).length ? (
              <>
                <p className="muted">正在更新 · 生成中预览，尚未形成正式纪要</p>
                {Object.entries(snapshot.minutes_preview.blocks)
                  .filter(
                    ([key]) =>
                      key.startsWith("overview.") ||
                      key.startsWith("conclusions."),
                  )
                  .map(([key, value]) => (
                    <p key={key}>{value}</p>
                  ))}
              </>
            ) : snapshot?.minutes?.narrative?.overview ? (
              <>
                <p>{snapshot.minutes.narrative.overview.text}</p>
                <p className="muted">
                  来自服务端{snapshot.status === "completed" ? "最终" : "阶段"}
                  纪要
                </p>
              </>
            ) : (
              <p className="muted">
                {snapshot?.chunks.length
                  ? "原文已接收，等待服务端形成阶段摘要。结构化事项会先后出现在下方。"
                  : "追加定稿转写后，摘要将在这里逐步形成。"}
              </p>
            )}
          </section>
          <div className="section-heading">
            <h2>结构化事项</h2>
            <span>
              当前 {current.length} · 待确认 {pending.length}
            </span>
          </div>
          <div className="filters" role="group" aria-label="事项筛选">
            {[
              ["active", `当前事项 ${current.length}`],
              ["pending", `待确认 ${pending.length}`],
              ["all", `全部 ${all.length}`],
            ].map(([key, label]) => (
              <button
                className={filter === key ? "selected" : ""}
                aria-pressed={filter === key}
                key={key}
                onClick={() => setFilter(key)}
              >
                {label}
              </button>
            ))}
          </div>
          <p className="filter-help">
            当前事项排除已被取代、撤销和已解决内容；不代表任务未完成。原文依据不等于客观事实或人工已验证。
          </p>
          {facts.length ? (
            Object.keys(kindNames).map((kind) => {
              const items = facts.filter((f) => f.kind === kind);
              return items.length ? (
                <section className="fact-group" key={kind}>
                  <h3>
                    {kindNames[kind]} <span>{items.length}</span>
                  </h3>
                  {items.map((f) => (
                    <article
                      className={`fact-row ${f.status === "superseded" ? "superseded" : ""}`}
                      key={f.id}
                    >
                      <p>{f.text}</p>
                      {(f.kind === "action" || f.owner || f.due) && (
                        <dl>
                          <div>
                            <dt>负责人</dt>
                            <dd>{f.owner || "未指定"}</dd>
                          </div>
                          <div>
                            <dt>期限</dt>
                            <dd>{f.due || "未明确"}</dd>
                          </div>
                        </dl>
                      )}
                      <div className="fact-meta">
                        <span>{factStatus[f.status] || f.status}</span>
                        <button
                          className="text-button"
                          onClick={() => evidence(f)}
                        >
                          查看依据
                          {f.source_ids.length > 1
                            ? ` · ${f.source_ids.length} 处`
                            : ""}
                        </button>
                      </div>
                    </article>
                  ))}
                </section>
              ) : null;
            })
          ) : (
            <div className="empty-state">
              <h3>
                {all.length
                  ? "此筛选下无匹配事项"
                  : snapshot?.chunks.length
                    ? "已接收原文，尚无可提取事项"
                    : "等待第一段转写"}
              </h3>
              <p>
                {all.length
                  ? "试试全部事项；已有内容并未删除。"
                  : "系统会保留已接收的原文。没有可提取的信息时不会凑出结论。"}
              </p>
              {all.length > 0 && (
                <button className="button" onClick={() => setFilter("all")}>
                  清除筛选
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
