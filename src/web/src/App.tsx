import { useEffect, useRef, useState } from "react";
import type { CSSProperties, FormEvent } from "react";
import { Menu, Moon, Sun, X } from "lucide-react";
import { api, statusText } from "./api";
import type { ChunkReceipt } from "./api";
import { ResultsPane } from "./components/ResultsPane";
import { Pipeline } from "./components/Pipeline";
import { Sidebar } from "./components/Sidebar";
import { TranscriptPane } from "./components/TranscriptPane";
import { Overlay } from "./components/Overlay";
import { useWorkspace } from "./hooks/useWorkspace";
import { useReplay } from "./hooks/useReplay";
import { charCount, exportMarkdown } from "./model";
const variants: Record<string, string> = {
  clean: "理想转写",
  colloquial: "重口语",
  asr_noisy: "识别噪声",
  formal: "正式语料",
};
export default function App() {
  const [theme, setTheme] = useState("vallum");
  const [error, setError] = useState("");
  const [feedback, setFeedback] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [text, setText] = useState("");
  const [tab, setTab] = useState("summaries");
  const [panel, setPanel] = useState("");
  const [nav, setNav] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  const [focus, setFocus] = useState(false);
  const [ratio, setRatio] = useState(40);
  const [sampleChoice, setSampleChoice] = useState("");
  const [evidence, setEvidence] = useState<{
    ids: string[];
    label: string;
  } | null>(null);
  const [includeEvidence, setIncludeEvidence] = useState(false);
  const [inputSource, setInputSource] = useState<Record<string, string>>({});
  const workspace = useWorkspace(setError);
  const playback = useReplay(setError);
  const { info, sessions, samples, selected, snapshot, events, connection } =
    workspace;
  const {
    speed,
    setSpeed,
    replaying,
    progress,
    pause,
    resume: runReplay,
  } = playback;
  const pendingSend = useRef<{ text: string; chunk_id: string } | null>(null);

  const sampleId = sampleChoice || samples[0]?.id || "";
  const sample = samples.find((s) => s.id === sampleId);
  const expired = connection === "会话已释放";
  const active = snapshot?.status === "running" && !expired;
  const count = charCount(text);
  const retention = Math.round((info?.retention_seconds || 300) / 60);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);
  async function action(fn: () => Promise<void>) {
    if (lock.current) return;
    lock.current = true;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      lock.current = false;
      setBusy(false);
    }
  }
  function selectSession(id: string) {
    playback.reset();
    workspace.select(id);
    setError("");
    setFeedback("");
    setNav(false);
    setPanel("");
    setEvidence(null);
    setTab("summaries");
  }
  async function newSession(title = "临时会议") {
    playback.reset();
    const id = await workspace.create(title);
    setTab("summaries");
    setNav(false);
    setPanel("append");
    return id;
  }
  async function startSample() {
    await playback.start(sampleId, async (title, date) => {
      const id = await workspace.create(title, date);
      setInputSource((s) => ({
        ...s,
        [id]: `样本回放 · ${variants[sample?.variant || ""] || "定稿转写"}`,
      }));
      return id;
    });
    setTab("summaries");
    setPanel("");
  }
  async function finish() {
    pause();
    await api(`/sessions/${selected}/finalize`, "POST");
    await workspace.refresh(selected);
    setTab("minutes");
  }
  async function remove() {
    pause();
    await api(`/sessions/${selected}`, "DELETE");
    selectSession("");
    await workspace.refreshSessions();
  }
  async function send() {
    if (!text.trim()) {
      throw new Error("请输入非空白的定稿转写。");
    }
    if (count > 16000)
      throw new Error(
        "单次最多 16000 个 Unicode 字符，请分段追加；当前内容已保留。",
      );
    const original = text;
    const id = selected || (await workspace.create("临时会议"));
    if (!pendingSend.current || pendingSend.current.text !== original)
      pendingSend.current = { text: original, chunk_id: crypto.randomUUID() };
    const receipt = await api<ChunkReceipt>(
      `/sessions/${id}/chunks`,
      "POST",
      pendingSend.current,
    );

    pendingSend.current = null;
    setText((current) => (current === original ? "" : current));
    setFeedback(
      `已追加片段 ${String(receipt.seq).padStart(2, "0")}${receipt.duplicate ? "（重复请求未再次追加）" : ""}`,
    );
    setInputSource((s) => ({ ...s, [id]: s[id] || "手工追加 · 定稿文本" }));
    setPanel("");
    await workspace.refresh(id);
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    void action(send);
  }
  function openEvidence(ids: string[], label = "原文依据") {
    setEvidence({ ids, label });
  }
  async function copy() {
    if (!snapshot) return;
    await navigator.clipboard.writeText(
      exportMarkdown(snapshot, includeEvidence),
    );
    setFeedback("纪要已复制；请保存在你选择的位置。");
  }
  function download() {
    if (!snapshot) return;
    const blob = new Blob([exportMarkdown(snapshot, includeEvidence)], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download =
      (snapshot.title || "会议纪要").replace(/[<>:"/\\|?*]/g, "_") + ".md";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    setFeedback("已发起 Markdown 下载，请确认浏览器下载结果。");
  }
  const sampleForm = (
    <div className="source-form">
      <h3>回放会议样本</h3>
      <p className="muted">
        选择定稿版本并新建临时会话。全部片段提交后自动收尾。
      </p>
      <label htmlFor="sample">会议样本</label>
      <select
        id="sample"
        value={sampleId}
        onChange={(e) => setSampleChoice(e.target.value)}
        disabled={busy || replaying}
      >
        {samples.map((s) => (
          <option key={s.id} value={s.id}>
            {s.title} · {variants[s.variant]}
          </option>
        ))}
      </select>
      {sample && (
        <p className="muted sample-description">
          {sample.title}
          <br />
          {variants[sample.variant]} · {sample.segments} 段 ·{" "}
          {sample.characters.toLocaleString()} 字
        </p>
      )}
      <label htmlFor="speed">回放速度</label>
      <select
        className="speed-select"
        id="speed"
        value={speed}
        onChange={(e) => setSpeed(Number(e.target.value))}
      >
        <option value={1}>1× · 约 12 字/秒</option>
        <option value={4}>4× · 快速观察</option>
        <option value={10}>10× · 加速回放</option>
        <option value={0}>立即提交全部片段</option>
      </select>
      <button
        className="button primary"
        disabled={!sampleId || busy || replaying}
        onClick={() => void action(startSample)}
      >
        {busy ? "正在准备…" : "新建并回放"}
      </button>
      {!samples.length && (
        <p>未找到样本。请检查服务端语料目录，或粘贴转写文本。</p>
      )}
      {progress.total > 0 && (
        <p>
          样本输入 {progress.current} / {progress.total} 段 ·{" "}
          {replaying
            ? "回放中"
            : progress.current < progress.total
              ? "已暂停"
              : "输入完成"}
          。摘要处理进度独立显示。
        </p>
      )}
    </div>
  );
  const sidebar = (
    <Sidebar
      sessions={sessions}
      selected={selected}
      busy={busy}
      onNew={() => void action(() => newSession().then(() => {}))}
      onSelect={selectSession}
      onClose={() => {
        setCollapsed(true);
        setNav(false);
      }}
    />
  );
  const canAppend = (!selected || active) && !busy;
  const transcript = (
    <TranscriptPane
      key={selected}
      snapshot={snapshot}
      onAppend={() => setPanel("append")}
      canAppend={canAppend}
    />
  );
  return (
    <div className={`app-shell ${collapsed ? "nav-collapsed" : ""}`}>
      <header className="topbar">
        <button
          className="icon-button"
          aria-label="打开会话导航"
          onClick={() => {
            setNav(true);
            setCollapsed(false);
          }}
        >
          <Menu size={20} />
        </button>
        <a className="brand" href="/">
          meetnote<span>.</span>
        </a>
        <span className="workspace-name">会议摘要验证与复核</span>
        <div className="top-actions">
          <span className="model-name">
            {info
              ? info.provider === "mock"
                ? "MOCK · 模拟模型"
                : info.model
              : "服务连接中"}
          </span>
          <button
            className="icon-button"
            aria-label={theme === "vallum" ? "切换苍渊主题" : "切换白垣主题"}
            onClick={() => setTheme(theme === "vallum" ? "abyssus" : "vallum")}
          >
            {theme === "vallum" ? <Moon size={18} /> : <Sun size={18} />}
          </button>
        </div>
      </header>
      <div className="shell-body">
        <div className="desktop-nav">{sidebar}</div>
        <main className="main">
          <div className="meeting-toolbar">
            <div>
              <h1>{snapshot?.title || "会议摘要工作台"}</h1>
              <p className="muted">
                {selected
                  ? `${inputSource[selected] || "定稿文本 · 来源未提供"} · ${expired ? "会话已不可访问" : snapshot ? statusText[snapshot.status] : "读取会话中"}`
                  : "输入转写、核对依据，获得可带走的会议纪要。"}
              </p>
            </div>
            {selected && (
              <div className="meeting-actions">
                <button className="button" onClick={() => setPanel("source")}>
                  输入来源
                </button>
                {active &&
                  progress.total > 0 &&
                  progress.current < progress.total && (
                    <button
                      className="button"
                      onClick={() => (replaying ? pause() : void runReplay())}
                    >
                      {replaying ? "暂停回放" : "继续回放"}
                    </button>
                  )}
                <button
                  className="button primary"
                  disabled={!active || busy}
                  onClick={() => void action(finish)}
                >
                  {snapshot?.status === "finalizing"
                    ? "正在收尾…"
                    : "收尾并生成纪要"}
                </button>
                <button
                  className="icon-button"
                  aria-label="取消并释放会话"
                  disabled={busy || expired}
                  onClick={() => setPanel("release")}
                >
                  <X size={18} />
                </button>
              </div>
            )}
          </div>
          {error && (
            <div className="error-banner" role="alert">
              <span>{error}</span>
              <button
                className="icon-button"
                aria-label="关闭错误提示"
                onClick={() => setError("")}
              >
                <X size={16} />
              </button>
            </div>
          )}
          {snapshot?.error && (
            <div className="error-banner" role="alert">
              处理未完成：{snapshot.error}
              。原文与已完成结果保留；未消费内容不会标记为已完成。
            </div>
          )}
          {selected && (
            <div className={`retention ${expired ? "error-banner" : ""}`}>
              {expired
                ? "会话已释放或服务已重启。以下仅为当前页面仍持有的内容，无法继续处理；请立即保存已有纪要。"
                : `临时会话：结束后保留 ${retention} 分钟；服务重启即释放。${snapshot?.status === "completed" ? "请及时复制或下载纪要。" : "不会自动归档。"}`}
              {snapshot?.minutes && (
                <button
                  className="text-button"
                  onClick={() => setPanel("export")}
                >
                  复制 / 保存
                </button>
              )}
            </div>
          )}
          {info?.provider === "mock" && (
            <div className="mock-notice">
              模拟模型 · 仅验证流程，结果不代表真实抽取质量。
            </div>
          )}
          <div className="feedback" role="status">
            {feedback}
          </div>
          {!selected ? (
            <div className="startup reading-scroll">
              <div className="startup-content">
                <h2>从一份转写开始</h2>
                {sampleForm}
                <div className="paste-path">
                  <h3>已有定稿转写？</h3>
                  <p>粘贴文本后会创建临时会话。不是音频采集或上传。</p>
                  <button className="button" onClick={() => setPanel("append")}>
                    粘贴转写文本
                  </button>
                </div>
                <p className="muted">
                  仅服务内存；结束后保留 {retention}{" "}
                  分钟，服务重启即释放。模型部署与原文数据流向取决于服务配置，不据此承诺数据不出网。
                </p>
              </div>
            </div>
          ) : (
            <>
              <div className="task-tabs" role="tablist" aria-label="工作视图">
                {[
                  ["summaries", "实时概览", "概览"],
                  ["transcript", "转写原文", "原文"],
                  ["minutes", "会议纪要", "纪要"],
                  ["history", "修订记录", "修订"],
                ].map(([key, label, mobile]) => (
                  <button
                    role="tab"
                    aria-selected={tab === key}
                    key={key}
                    className={`${tab === key ? "active" : ""} ${key === "transcript" ? "mobile-transcript-tab" : ""}`}
                    onClick={() => setTab(key)}
                  >
                    <span className="desktop-label">{label}</span>
                    <span className="mobile-label">{mobile}</span>
                  </button>
                ))}
                {tab === "summaries" && (
                  <button
                    className="focus-button"
                    onClick={() => setFocus(!focus)}
                  >
                    {focus ? "恢复双栏" : "聚焦结果"}
                  </button>
                )}
                <button
                  className="append-shortcut"
                  onClick={() => setPanel("append")}
                  disabled={!canAppend}
                >
                  追加转写
                </button>
              </div>
              <div
                className={`workspace ${tab === "summaries" && !focus ? "split" : ""}`}
                style={{ "--transcript-ratio": `${ratio}%` } as CSSProperties}
              >
                <div
                  className={`transcript-region ${tab === "summaries" && !focus ? "in-overview" : tab === "transcript" ? "standalone" : "parked"}`}
                >
                  {transcript}
                </div>
                {tab === "summaries" && !focus && (
                  <div
                    className="resize-handle"
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="调整原文宽度"
                    aria-valuemin={32}
                    aria-valuemax={52}
                    aria-valuenow={ratio}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
                        e.preventDefault();
                        setRatio((r) =>
                          Math.max(
                            32,
                            Math.min(52, r + (e.key === "ArrowLeft" ? -2 : 2)),
                          ),
                        );
                      }
                    }}
                    onPointerDown={(e) => {
                      e.currentTarget.setPointerCapture(e.pointerId);
                    }}
                    onPointerMove={(e) => {
                      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                        const parent =
                          e.currentTarget.parentElement!.getBoundingClientRect();
                        setRatio(
                          Math.max(
                            32,
                            Math.min(
                              52,
                              ((e.clientX - parent.left) / parent.width) * 100,
                            ),
                          ),
                        );
                      }
                    }}
                    onPointerUp={(e) =>
                      e.currentTarget.releasePointerCapture(e.pointerId)
                    }
                  />
                )}
                <div
                  className={`result-region ${tab === "transcript" ? "parked" : ""}`}
                >
                  <ResultsPane
                    snapshot={snapshot}
                    tab={tab}
                    focusEvidence={openEvidence}
                    onFinish={() => void action(finish)}
                    onExport={() => setPanel("export")}
                  />
                </div>
              </div>
            </>
          )}
        </main>
      </div>
      <footer className="statusbar">
        <span>
          {selected
            ? `已接收 ${snapshot?.accepted_count || 0} 段 · 已处理 ${snapshot?.processed_count || 0} 段${snapshot?.remaining_chars ? ` · 待处理 ${snapshot.remaining_chars} 字` : ""}`
            : "等待输入"}
        </span>
        <span className="connection">
          {selected ? connection : "未订阅会话事件"}
          {replaying ? " · 样本回放中" : ""}
        </span>
        {selected && (
          <button
            className="text-button"
            onClick={() => setPanel("diagnostic")}
          >
            运行诊断
          </button>
        )}
      </footer>
      {nav && (
        <Overlay title="临时会话" onClose={() => setNav(false)}>
          {sidebar}
        </Overlay>
      )}
      {panel === "source" && (
        <Overlay title="输入来源与回放设置" onClose={() => setPanel("")}>
          <p>当前输入：{inputSource[selected] || "定稿文本，来源未提供"}</p>
          {sampleForm}
          <p className="muted">此处重新启动会新建会话，不覆盖当前原文。</p>
        </Overlay>
      )}
      {panel === "append" && (
        <Overlay title="追加定稿转写" onClose={() => setPanel("")} wide>
          <form onSubmit={submit}>
            <p>
              追加已完成识别、不再实时改写的文本。
              {!selected ? "首次追加会新建临时会话。" : ""}
            </p>
            <label htmlFor="text-input">转写文本</label>
            <textarea
              autoFocus
              id="text-input"
              value={text}
              disabled={busy || (!!selected && !active)}
              aria-invalid={count > 16000}
              aria-describedby="input-help"
              placeholder="例如：时间改为周四验收，周五发布，仍由林岚负责。"
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (
                  (e.ctrlKey || e.metaKey) &&
                  e.key === "Enter" &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  if (!busy) void action(send);
                }
              }}
            />
            <div className="editor-meta" id="input-help">
              <span>
                {count.toLocaleString()} / 16000 字符（按 Unicode 字符计数）
              </span>
              <span>Ctrl / ⌘ + Enter 追加</span>
            </div>
            {count > 16000 && (
              <p role="alert">内容超限，请分段追加；不会截断输入。</p>
            )}
            {selected && !active && (
              <p>当前会话已停止接收。请保留输入并新建会话。</p>
            )}
            {error && <p role="alert">{error} 输入已保留。</p>}
            <button
              className="button primary"
              disabled={!canAppend || count > 16000 || !text.trim()}
            >
              {busy ? "正在追加…" : "追加转写"}
            </button>
            <p className="muted">无音频上传或录音能力。Enter 保留换行。</p>
          </form>
        </Overlay>
      )}
      {evidence && (
        <Overlay title="原文依据" onClose={() => setEvidence(null)}>
          <button className="button" onClick={() => setEvidence(null)}>
            ← 返回此前事项
          </button>
          <h3>{evidence.label}</h3>
          <p className="muted">
            引用按服务端原文证据展示；不等于客观事实或人工已验证。片段字段缺失时不补时间。
          </p>
          {!evidence.ids.length && (
            <p role="alert">该事项没有可用引用。请保留结果并检查处理事件。</p>
          )}
          {evidence.ids.map((id) => {
            const source = snapshot?.sources.find((s) => s.id === id);
            const chunk = snapshot?.chunks.find(
              (c) => c.chunk_id === source?.chunk_id,
            );
            const index =
              snapshot?.chunks.findIndex(
                (c) => c.chunk_id === source?.chunk_id,
              ) ?? -1;
            return (
              <article className="evidence-quote" key={id}>
                <h4>
                  {chunk
                    ? `片段 ${String(chunk.seq).padStart(2, "0")} · ${chunk.speaker || "发言人未提供"}`
                    : `依据 ${id}`}
                </h4>
                {chunk?.timestamp && <p>{chunk.timestamp}</p>}
                {source ? (
                  <>
                    <blockquote>{source.text}</blockquote>
                    <p className="muted">
                      服务端引用文本 · 来源标识 {id}。未在界面标注精确字符位置。
                    </p>
                    {chunk && source.text !== chunk.text && (
                      <details>
                        <summary>完整片段</summary>
                        <p>{chunk.text}</p>
                      </details>
                    )}
                    {index > 0 && (
                      <details>
                        <summary>前一片段上下文</summary>
                        <p>{snapshot!.chunks[index - 1].text}</p>
                      </details>
                    )}
                  </>
                ) : (
                  <p role="alert">
                    无法定位该依据：可能已释放或引用缺失。保留事项，不假装已定位。
                  </p>
                )}
              </article>
            );
          })}
        </Overlay>
      )}
      {panel === "export" && (
        <Overlay title="复制或保存已有纪要" onClose={() => setPanel("")}>
          <p>
            包含正文与事项清单，默认不包含原文依据，保留待确认说明。
            {snapshot?.status !== "completed"
              ? "当前只能保存阶段纪要，不是完整最终结果。"
              : ""}
          </p>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={includeEvidence}
              onChange={(e) => setIncludeEvidence(e.target.checked)}
            />
            附上引用原文（会增加导出内容）
          </label>
          <div className="export-actions">
            <button
              className="button"
              onClick={() => void action(copy)}
              disabled={busy}
            >
              复制正文
            </button>
            <button className="button primary" onClick={download}>
              下载 Markdown
            </button>
          </div>
          {error && <p role="alert">{error} 已有纪要仍保留，可尝试下载。</p>}
          <p className="muted">不会建立长期档案或云端分享。</p>
        </Overlay>
      )}
      {panel === "diagnostic" && (
        <Overlay title="处理链路与运行事件" onClose={() => setPanel("")} wide>
          <Pipeline
            info={info}
            snapshot={snapshot}
            events={events}
            connection={connection}
          />
        </Overlay>
      )}
      {panel === "release" && (
        <Overlay title="释放临时会话" onClose={() => setPanel("")}>
          <p>这会取消处理并释放服务端会话。请先保存需要的纪要或原文。</p>
          <button className="button" onClick={() => setPanel("")}>
            返回会话
          </button>
          <button
            className="button primary"
            onClick={() => void action(remove)}
            disabled={busy}
          >
            确认释放会话
          </button>
        </Overlay>
      )}
    </div>
  );
}
