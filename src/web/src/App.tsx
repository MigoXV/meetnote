import { useEffect, useState } from "react";
import {
  Check,
  Layers3,
  Moon,
  Pause,
  Play,
  Radio,
  Square,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import { api, statusText } from "./api";
import { ResultsPane } from "./components/ResultsPane";
import { Pipeline } from "./components/Pipeline";
import { Sidebar } from "./components/Sidebar";
import { TranscriptPane } from "./components/TranscriptPane";
import { useWorkspace } from "./hooks/useWorkspace";
import { useReplay } from "./hooks/useReplay";

export default function App() {
  const [theme, setTheme] = useState("abyssus");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [text, setText] = useState("");
  const [tab, setTab] = useState("summaries");
  const [sampleChoice, setSampleChoice] = useState("");
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
  const sampleId = sampleChoice || samples[0]?.id || "";
  const active = snapshot?.status === "running";

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  async function action(fn: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  function selectSession(id: string) {
    playback.reset();
    workspace.select(id);
    setError("");
  }
  async function newSession(title = "临时会议") {
    playback.reset();
    return workspace.create(title);
  }
  async function startSample() {
    await playback.start(sampleId, workspace.create);
  }
  async function finish() {
    pause();
    await api(`/sessions/${selected}/finalize`, "POST");
    await workspace.refresh(selected);
  }
  async function remove() {
    pause();
    await api(`/sessions/${selected}`, "DELETE");
    selectSession("");
    await workspace.refreshSessions();
  }
  async function send() {
    const id = selected || (await newSession());
    await api(`/sessions/${id}/chunks`, "POST", {
      text,
      chunk_id: crypto.randomUUID(),
    });
    setText("");
    await workspace.refresh(id);
  }
  function focusEvidence(ids: string[]) {
    if (ids[0])
      document
        .getElementById(`chunk-${ids[0]}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="MeetNote 首页">
          <Layers3 size={22} />
          <span>
            meetnote<span className="brand-dot">.</span>
          </span>
        </a>
        <div className="workspace-name">
          <span className="separator">/</span>会议摘要工作台
          <span className="tag">SIDECAR</span>
        </div>
        <div className="top-actions">
          <span className="model-name">
            <Radio size={13} />
            {info
              ? info.provider === "mock"
                ? "MOCK · 模拟模型"
                : info.model
              : "连接服务…"}
          </span>
          <button
            className="icon-button"
            aria-label={theme === "abyssus" ? "切换白垣主题" : "切换苍渊主题"}
            onClick={() => setTheme(theme === "abyssus" ? "vallum" : "abyssus")}
          >
            {theme === "abyssus" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
        </div>
      </header>

      <Sidebar
        sessions={sessions}
        selected={selected}
        samples={samples}
        sampleId={sampleId}
        replaying={replaying}
        speed={speed}
        busy={busy}
        info={info}
        onNew={() =>
          void action(async () => {
            await newSession();
          })
        }
        onSelect={selectSession}
        onSampleChange={setSampleChoice}
        onSpeedChange={setSpeed}
        onStart={() => void action(startSample)}
      />

      <main className="main">
        <div className="page-heading">
          <div>
            <div className="eyebrow">LIVE MEETING / 实时会议</div>
            <h1>{snapshot?.title || "让讨论，逐渐清晰。"}</h1>
            <p className="muted">
              {snapshot
                ? `${statusText[snapshot.status]} · ${snapshot.accepted_count} 段已接收 · ${snapshot.processed_count} 段已处理`
                : "输入定稿转写，观察语义信号如何成为有据可查的会议纪要。"}
            </p>
          </div>
          <div className="meeting-actions">
            {selected && (
              <>
                {active &&
                  progress.total > 0 &&
                  progress.current < progress.total && (
                    <button
                      className="button"
                      onClick={() => (replaying ? pause() : void runReplay())}
                    >
                      {replaying ? <Pause size={15} /> : <Play size={15} />}{" "}
                      {replaying ? "暂停回放" : "继续回放"}
                    </button>
                  )}
                <button
                  className="button"
                  disabled={!active || busy}
                  onClick={() => void action(finish)}
                >
                  <Check size={15} />
                  收尾
                </button>
                <button
                  className="icon-button"
                  aria-label="取消并释放会话"
                  disabled={busy}
                  onClick={() => void action(remove)}
                >
                  {active ? <Square size={16} /> : <Trash2 size={16} />}
                </button>
              </>
            )}
          </div>
        </div>
        {error && (
          <div className="error-banner" role="alert">
            <span>{error}</span>
            <button
              className="icon-button"
              aria-label="关闭错误提示"
              onClick={() => setError("")}
            >
              <X size={15} />
            </button>
          </div>
        )}
        {snapshot?.error && (
          <div className="error-banner" role="alert">
            处理未完成：{snapshot.error}。已接收原文仍可查看。
          </div>
        )}
        {info?.provider === "mock" && (
          <div className="notice">当前使用模拟模型，结果仅用于验证流程。</div>
        )}
        {progress.total > 0 && (
          <div className="replay-progress">
            <span>
              样本回放 {progress.current} / {progress.total}
            </span>
            <progress max={progress.total} value={progress.current} />
            <span>
              {replaying
                ? "回放中"
                : progress.current === progress.total
                  ? "输入完成"
                  : "已暂停"}
            </span>
          </div>
        )}

        <div className="workspace">
          <TranscriptPane
            snapshot={snapshot}
            selected={selected}
            active={active}
            text={text}
            busy={busy}
            setText={setText}
            onSend={() => action(send)}
          />

          <ResultsPane
            snapshot={snapshot}
            tab={tab}
            setTab={setTab}
            focusEvidence={focusEvidence}
          />
        </div>

        <Pipeline
          info={info}
          snapshot={snapshot}
          events={events}
          connection={connection}
        />

        <footer className="main-footer">
          <span>MEETNOTE / 会议摘要 SIDECAR</span>
          <span>输入 → 信号 → 证据 → 纪要</span>
        </footer>
      </main>
    </div>
  );
}
