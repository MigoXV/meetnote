import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Pause,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Square,
  Trash2,
} from "lucide-react";
import { Client, errorText, sessionPath } from "./api";
import { Copy, Document, Empty, Json, Notice, SamplePicker } from "./ui";
import {
  statusText,
  terminal,
  type Receipt,
  type SessionRow,
  type Snapshot,
  type Transcript,
  type WireEvent,
} from "./types";

export default function Sessions({
  client,
  onBusy,
}: {
  client: Client;
  onBusy: (busy: boolean) => void;
}) {
  const [rows, setRows] = useState<SessionRow[]>([]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [title, setTitle] = useState("");
  const [date, setDate] = useState("");
  const [text, setText] = useState("");
  const [speaker, setSpeaker] = useState("");
  const [timestamp, setTimestamp] = useState("");
  const [chunkId, setChunkId] = useState<string>(() => crypto.randomUUID());
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [sample, setSample] = useState<Transcript | null>(null);
  const [replayIndex, setReplayIndex] = useState(0);
  const [replaying, setReplaying] = useState(false);
  const [interval, setIntervalMs] = useState("500");
  const [tab, setTab] = useState("minutes");
  const [events, setEvents] = useState<
    { id: number; type: string; detail: string }[]
  >([]);
  const [subscribed, setSubscribed] = useState(true);
  const [stream, setStream] = useState("等待会话");
  const [streamError, setStreamError] = useState("");
  const [epoch, setEpoch] = useState(0);
  const [cursorInput, setCursorInput] = useState("");
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [preview, setPreview] = useState<Record<string, string> | null>(null);
  const cursor = useRef<number | null>(null);
  const replayToken = useRef("");
  const operation = useRef<AbortController | null>(null);
  const sid = snapshot?.id;
  const accepting = snapshot?.status === "running";

  useEffect(() => () => operation.current?.abort(), []);
  useEffect(() => {
    const controller = new AbortController();
    client
      .request<SessionRow[]>(
        "/api/v1/sessions",
        "GET",
        undefined,
        controller.signal,
      )
      .then(setRows)
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorText(e));
      });
    return () => controller.abort();
  }, [client]);
  useEffect(() => {
    if (!sid || !subscribed) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      if (timer) return;
      timer = setTimeout(() => {
        timer = undefined;
        client
          .request<Snapshot>(
            sessionPath(sid),
            "GET",
            undefined,
            controller.signal,
          )
          .then((next) =>
            setSnapshot((old) =>
              old?.id === next.id && old.event_cursor <= next.event_cursor
                ? next
                : old,
            ),
          )
          .catch((e) => {
            if (!controller.signal.aborted) setStreamError(errorText(e));
          });
      }, 200);
    };
    client
      .events(sid, cursor.current, controller.signal, (event: WireEvent) => {
        if (controller.signal.aborted) return;
        cursor.current = event.id;
        setStream("接收中");
        setStreamError("");
        setEvents((old) => [
          ...old.slice(-59),
          {
            id: event.id,
            type: event.type,
            detail: JSON.stringify(event.data).slice(0, 2000),
          },
        ]);
        if (event.type === "session.snapshot") {
          const next = event.data as unknown as Snapshot;
          setSnapshot((old) =>
            old?.id === next.id && old.event_cursor <= next.event_cursor
              ? next
              : old,
          );
          setPreview(next.minutes_preview?.blocks ?? null);
        } else if (
          event.type === "minutes.delta" ||
          event.type === "minutes.started"
        ) {
          setPreview((event.data.blocks as Record<string, string>) ?? null);
        } else {
          if (
            [
              "minutes.updated",
              "minutes.completed",
              "minutes.aborted",
            ].includes(event.type)
          )
            setPreview(null);
          if (
            [
              "input.accepted",
              "input.processed",
              "facts.updated",
              "minutes.updated",
              "minutes.completed",
              "session.terminal",
              "session.finalizing",
            ].includes(event.type)
          )
            refresh();
        }
      })
      .then(() => {
        if (!controller.signal.aborted) setStream("连接已结束");
      })
      .catch((e) => {
        if (!controller.signal.aborted) {
          setStream("连接中断");
          setStreamError(errorText(e));
        }
      });
    return () => {
      controller.abort();
      if (timer) clearTimeout(timer);
    };
  }, [client, sid, subscribed, epoch]);

  async function run(work: (signal: AbortSignal) => Promise<void>) {
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      await work(controller.signal);
    } catch (e) {
      if (!controller.signal.aborted) setError(errorText(e));
    } finally {
      setBusy(false);
      onBusy(false);
      setReplaying(false);
      operation.current = null;
    }
  }
  async function refreshRows(signal?: AbortSignal) {
    setRows(
      await client.request<SessionRow[]>(
        "/api/v1/sessions",
        "GET",
        undefined,
        signal,
      ),
    );
  }
  async function refreshSnapshot(id: string, signal?: AbortSignal) {
    const next = await client.request<Snapshot>(
      sessionPath(id),
      "GET",
      undefined,
      signal,
    );
    setSnapshot((old) =>
      old?.id === next.id && old.event_cursor > next.event_cursor ? old : next,
    );
  }
  function select(next: Snapshot) {
    cursor.current = null;
    setSnapshot(next);
    setPreview(next.minutes_preview?.blocks ?? null);
    setEvents([]);
    setReceipt(null);
    setStream("正在订阅");
    setStreamError("");
    setSubscribed(true);
    setEpoch((n) => n + 1);
    setConfirmDelete(false);
    setCursorInput("");
    replayToken.current = crypto.randomUUID();
    setReplayIndex(0);
  }
  async function replay(signal: AbortSignal) {
    if (!snapshot || !sample) return;
    setReplaying(true);
    for (let i = replayIndex; i < sample.segments.length; i++) {
      signal.throwIfAborted();
      const segment = sample.segments[i];
      const response = await client.request<Receipt>(
        sessionPath(snapshot.id) + "/chunks",
        "POST",
        {
          chunk_id: `demo-${replayToken.current}-${i}`,
          text: segment.text,
          speaker: segment.speaker || null,
        },
        signal,
      );
      setReceipt(response);
      setReplayIndex(i + 1);
      if (Number(interval) && i < sample.segments.length - 1) {
        await new Promise<void>((resolve, reject) => {
          const abort = () => {
            clearTimeout(timer);
            reject(new DOMException("回放已暂停", "AbortError"));
          };
          const timer = setTimeout(() => {
            signal.removeEventListener("abort", abort);
            resolve();
          }, Number(interval));
          signal.addEventListener("abort", abort, { once: true });
          if (signal.aborted) abort();
        });
      }
    }
    await refreshSnapshot(snapshot.id, signal);
    await refreshRows(signal);
  }
  const previewText = preview ? Object.values(preview).join("\n\n") : "";
  return (
    <div className="workbench session-workbench">
      <section className="input-pane" aria-label="会话操作">
        <div className="section-heading">
          <h2>会话列表</h2>
          <button
            className="quiet small"
            disabled={busy}
            onClick={() => void run((signal) => refreshRows(signal))}
          >
            <RefreshCw size={14} />
            刷新列表
          </button>
        </div>
        <div className="session-list">
          {rows.length ? (
            rows.map((row) => (
              <button
                key={row.id}
                className={
                  snapshot?.id === row.id
                    ? "session-row selected"
                    : "session-row"
                }
                aria-pressed={snapshot?.id === row.id}
                disabled={busy}
                onClick={() =>
                  void run(async (signal) =>
                    select(
                      await client.request<Snapshot>(
                        sessionPath(row.id),
                        "GET",
                        undefined,
                        signal,
                      ),
                    ),
                  )
                }
              >
                <span>
                  <strong>{row.title || "未命名会话"}</strong>
                  <small>
                    {row.id.slice(0, 12)} · {row.accepted_count} 段
                  </small>
                </span>
                <span>
                  {
                    statusText[
                      row.id === snapshot?.id ? snapshot.status : row.status
                    ]
                  }
                </span>
              </button>
            ))
          ) : (
            <p className="hint">实例中暂时没有会话。可在下方创建。</p>
          )}
        </div>
        <details className="create-session" open={!snapshot || undefined}>
          <summary>创建新会话</summary>
          <div className="form-stack">
            <label>
              会议标题
              <input
                value={title}
                maxLength={200}
                disabled={busy}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="例如：产品周会"
              />
            </label>
            <label>
              会议日期
              <input
                type="date"
                value={date}
                disabled={busy}
                onChange={(e) => setDate(e.target.value)}
              />
            </label>
            <button
              className="secondary"
              disabled={busy}
              onClick={() =>
                void run(async (signal) => {
                  select(
                    await client.request<Snapshot>(
                      "/api/v1/sessions",
                      "POST",
                      { title, meeting_date: date },
                      signal,
                    ),
                  );
                  await refreshRows(signal);
                })
              }
            >
              <Plus size={16} />
              创建会话
            </button>
          </div>
        </details>
        <div className="section-heading">
          <h2>追加转写</h2>
          <span>按接收顺序处理</span>
        </div>
        {!snapshot && (
          <p className="hint">先创建或选择一个会话，再提交文本。</p>
        )}
        <fieldset disabled={busy || !accepting} className="form-stack">
          <label>
            定稿片段
            <textarea
              value={text}
              maxLength={16000}
              rows={5}
              onChange={(e) => setText(e.target.value)}
              placeholder="输入一段已结束的发言…"
            />
          </label>
          <div className="two-fields">
            <label>
              发言人
              <input
                value={speaker}
                maxLength={200}
                onChange={(e) => setSpeaker(e.target.value)}
                placeholder="可选"
              />
            </label>
            <label>
              时间戳
              <input
                value={timestamp}
                maxLength={100}
                onChange={(e) => setTimestamp(e.target.value)}
                placeholder="可选"
              />
            </label>
          </div>
          <label>
            片段 ID
            <input
              className="mono"
              value={chunkId}
              maxLength={128}
              onChange={(e) => setChunkId(e.target.value)}
            />
          </label>
          <p className="hint">
            失败后保留 ID 以便幂等重试。提交成功仅表示已入队。
          </p>
          <button
            className="primary"
            disabled={!text.trim() || !chunkId.trim()}
            onClick={() =>
              void run(async (signal) => {
                if (!snapshot) return;
                const received = await client.request<Receipt>(
                  sessionPath(snapshot.id) + "/chunks",
                  "POST",
                  {
                    text,
                    chunk_id: chunkId,
                    speaker: speaker || null,
                    timestamp: timestamp || null,
                  },
                  signal,
                );
                setReceipt(received);
                setText("");
                setChunkId(crypto.randomUUID());
                await refreshSnapshot(snapshot.id, signal);
                await refreshRows(signal);
              })
            }
          >
            <ArrowRight size={16} />
            提交片段
          </button>
        </fieldset>
        {receipt && (
          <p className="receipt" role="status">
            第 {receipt.seq} 段 ·{" "}
            {receipt.duplicate ? "幂等重复，未再次入队" : "已接受入队"}
            <code>{receipt.chunk_id}</code>
          </p>
        )}
        <div className="section-heading">
          <h2>样本回放</h2>
          <span>逐段调用 chunks</span>
        </div>
        <SamplePicker
          client={client}
          disabled={busy}
          onLoad={(value) => {
            setSample(value);
            setReplayIndex(0);
            replayToken.current = crypto.randomUUID();
            if (!snapshot) {
              setTitle(value.title);
              setDate(value.meeting_date);
            }
          }}
        />
        {sample && (
          <p className="hint">
            {sample.title} · 已接受 {replayIndex} / {sample.segments.length} 段
          </p>
        )}
        <div className="inline">
          <label>
            片段间隔
            <select
              value={interval}
              disabled={busy}
              onChange={(e) => setIntervalMs(e.target.value)}
            >
              <option value="0">立即发送</option>
              <option value="500">500 毫秒</option>
              <option value="1500">1.5 秒</option>
            </select>
          </label>
          <button
            className="secondary"
            disabled={
              !sample ||
              !accepting ||
              busy ||
              replayIndex === sample.segments.length
            }
            onClick={() => void run(replay)}
          >
            <Play size={15} />
            {replayIndex ? "继续回放" : "逐段回放"}
          </button>
          {replaying && (
            <button
              className="quiet"
              onClick={() => operation.current?.abort()}
            >
              <Pause size={15} />
              暂停回放
            </button>
          )}
        </div>
        <p className="hint">回放结束后仍需点击“结束输入”，才会触发最终收尾。</p>
        {error && <Notice error>{error}</Notice>}
      </section>
      <section className="output-pane" aria-labelledby="session-output-title">
        <div className="section-heading">
          <h2 id="session-output-title">{snapshot?.title || "会话工作区"}</h2>
          <span role="status">
            {snapshot ? statusText[snapshot.status] : "未选择会话"}
          </span>
        </div>
        {!snapshot ? (
          <Empty title="从一段发言开始">
            创建会话、追加转写，再观察事实与纪要如何形成。可随时连接实例中已有的会话。
          </Empty>
        ) : (
          <>
            <p className="session-id mono">{snapshot.id}</p>
            <div className="metrics">
              <span>
                <strong>{snapshot.accepted_count}</strong>已接受片段
              </span>
              <span>
                <strong>{snapshot.processed_count}</strong>消费者已取
              </span>
              <span>
                <strong>{snapshot.remaining_chars}</strong>剩余字符
              </span>
              <span>
                <strong>{snapshot.queue_size}</strong>队列待取
              </span>
            </div>
            <div className="action-row session-actions">
              <button
                className="primary"
                disabled={busy || !accepting}
                onClick={() =>
                  void run(async (signal) => {
                    await client.request(
                      sessionPath(snapshot.id) + "/finalize",
                      "POST",
                      undefined,
                      signal,
                    );
                    await refreshSnapshot(snapshot.id, signal);
                  })
                }
              >
                <Square size={14} />
                结束输入
              </button>
              <button
                className="secondary"
                disabled={busy}
                onClick={() =>
                  void run((signal) => refreshSnapshot(snapshot.id, signal))
                }
              >
                <RefreshCw size={15} />
                刷新快照
              </button>
              <button
                className="quiet"
                disabled={busy}
                onClick={() => setConfirmDelete(true)}
              >
                <Trash2 size={15} />
                释放会话
              </button>
            </div>
            {confirmDelete && (
              <Notice>
                <strong>释放“{snapshot.title || snapshot.id}”？</strong>
                <p>这会取消未完成处理并删除实例中的该会话，无法恢复。</p>
                <div className="inline">
                  <button
                    className="secondary danger"
                    disabled={busy}
                    onClick={() =>
                      void run(async (signal) => {
                        await client.request(
                          sessionPath(snapshot.id),
                          "DELETE",
                          undefined,
                          signal,
                        );
                        setSnapshot(null);
                        setPreview(null);
                        setEvents([]);
                        setReceipt(null);
                        setConfirmDelete(false);
                        await refreshRows(signal);
                      })
                    }
                  >
                    确认释放
                  </button>
                  <button
                    className="quiet"
                    onClick={() => setConfirmDelete(false)}
                  >
                    返回
                  </button>
                </div>
              </Notice>
            )}
            {snapshot.error && (
              <Notice error>
                实例处理失败：{snapshot.error}。可查看原文、事实和快照定位问题。
              </Notice>
            )}
            {!!snapshot.warnings.length && (
              <Notice>{snapshot.warnings.join("；")}</Notice>
            )}
            <div className="stream-bar">
              <Radio size={15} />
              <span>
                SSE · {stream}
                {terminal(snapshot.status) ? " · 会话已终止" : ""}
              </span>
              <button
                className="quiet small"
                onClick={() => {
                  setSubscribed(!subscribed);
                  setStream(subscribed ? "订阅已暂停" : "正在订阅");
                }}
              >
                {subscribed ? "暂停订阅" : "恢复订阅"}
              </button>
            </div>
            {streamError && (
              <Notice error>
                订阅异常：{streamError}。可刷新快照或重连补发。
              </Notice>
            )}
            <div className="tabs" role="group" aria-label="会话结果">
              {[
                ["minutes", "纪要"],
                ["facts", `事实 ${snapshot.facts.length}`],
                ["chunks", `原文 ${snapshot.chunks.length}`],
                ["events", `事件 ${events.length}`],
              ].map(([key, label]) => (
                <button
                  aria-pressed={tab === key}
                  key={key}
                  onClick={() => setTab(key)}
                >
                  {label}
                </button>
              ))}
            </div>
            {tab === "minutes" && (
              <>
                {previewText && (
                  <section className="preview">
                    <p className="eyebrow">正在生成 · 可撤回预览</p>
                    <Document text={previewText} />
                  </section>
                )}
                {snapshot.minutes ? (
                  <>
                    <div className="document-toolbar">
                      <span>已通过校验的纪要</span>
                      <Copy text={snapshot.minutes.markdown} />
                    </div>
                    <Document text={snapshot.minutes.markdown} />
                  </>
                ) : (
                  !previewText && (
                    <Empty
                      title={
                        snapshot.status === "failed"
                          ? "纪要未能完成"
                          : "纪要尚未生成"
                      }
                    >
                      {accepting
                        ? "追加定稿片段后，实例会整理已确认的证据。"
                        : "请结合会话状态与错误信息检查处理结果。"}
                    </Empty>
                  )
                )}
              </>
            )}
            {tab === "facts" && (
              <div className="fact-list">
                {snapshot.facts.length ? (
                  snapshot.facts.map((fact) => (
                    <article key={fact.id}>
                      <div className="section-heading compact">
                        <code>
                          {fact.id} · r{fact.revision}
                        </code>
                        <span>
                          {fact.kind} / {fact.status}
                        </span>
                      </div>
                      <p>{fact.text}</p>
                      <p className="hint">
                        负责人：{fact.owner || "未明确"} · 期限：
                        {fact.due || "未明确"}
                      </p>
                      <small>原文依据：{fact.source_ids.join("、")}</small>
                    </article>
                  ))
                ) : (
                  <Empty title="暂无已提交事实">
                    事实仅在证据核验和正式提交后显示。
                  </Empty>
                )}
              </div>
            )}
            {tab === "chunks" && (
              <div className="chunk-list">
                {snapshot.chunks.length ? (
                  snapshot.chunks.map((chunk) => (
                    <article key={chunk.chunk_id}>
                      <div className="section-heading compact">
                        <span>
                          {String(chunk.seq).padStart(2, "0")} /{" "}
                          {chunk.speaker || "未标注发言人"}
                        </span>
                        <code>{chunk.chunk_id}</code>
                      </div>
                      <p>{chunk.text}</p>
                    </article>
                  ))
                ) : (
                  <Empty title="尚未接受转写">
                    在左侧提交片段，或载入样本后逐段回放。
                  </Empty>
                )}
              </div>
            )}
            {tab === "events" && (
              <>
                <div className="inline cursor-controls">
                  <label>
                    补发游标
                    <input
                      type="number"
                      min="0"
                      step="1"
                      value={cursorInput}
                      onChange={(e) => setCursorInput(e.target.value)}
                      placeholder="自动使用最近 ID"
                    />
                  </label>
                  <button
                    className="secondary"
                    onClick={() => {
                      const value =
                        cursorInput === ""
                          ? cursor.current
                          : Number(cursorInput);
                      if (
                        value !== null &&
                        (!Number.isSafeInteger(value) || value < 0)
                      ) {
                        setStreamError("补发游标必须是非负整数。");
                        return;
                      }
                      cursor.current = value;
                      setSubscribed(true);
                      setStream("正在重连");
                      setStreamError("");
                      setEpoch((n) => n + 1);
                    }}
                  >
                    重连并补发
                  </button>
                  <button className="quiet small" onClick={() => setEvents([])}>
                    清空本地事件
                  </button>
                </div>
                <p className="hint">
                  保留最近 60 条事件，每条仅展示前 2,000
                  字符。游标过期时由实例返回完整快照。
                </p>
                <div className="event-list">
                  {events.map((event, index) => (
                    <details key={`${event.id}-${index}`}>
                      <summary>
                        <code>#{event.id}</code>
                        <span>{event.type}</span>
                      </summary>
                      <pre>{event.detail}</pre>
                    </details>
                  ))}
                </div>
              </>
            )}
            <Json value={snapshot} label="完整快照 · 来源、修订与模型统计" />
            <p className="hint lifecycle-note">
              离开页面只会断开客户端订阅；服务端会话会继续处理，直至释放或过期。
            </p>
          </>
        )}
      </section>
    </div>
  );
}
