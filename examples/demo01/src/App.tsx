import { useCallback, useEffect, useRef, useState } from "react";
import {
  ArrowUpRight,
  Check,
  Circle,
  FileText,
  Layers3,
  Moon,
  Plug,
  Radio,
  Sun,
} from "lucide-react";
import { Client, errorText, normalizeTarget } from "./api";
import Diagnostics from "./Diagnostics";
import Fulltext from "./Fulltext";
import Sessions from "./Sessions";
import { Empty, Notice } from "./ui";
import type { Info, RequestLog } from "./types";

type View = "fulltext" | "sessions" | "diagnostics";
const views = [
  {
    id: "fulltext" as const,
    label: "全文生成",
    icon: FileText,
    description: "提交完整转写，验证摘要、纪要、润色与工作计划。",
  },
  {
    id: "sessions" as const,
    label: "流式会话",
    icon: Radio,
    description: "逐段输入，订阅实时事件，观察证据与纪要的形成。",
  },
  {
    id: "diagnostics" as const,
    label: "实例诊断",
    icon: Layers3,
    description: "检查服务能力、接口定义与本次客户端请求。",
  },
];

export default function App() {
  const [view, setView] = useState<View>("fulltext");
  const [theme, setTheme] = useState<"vallum" | "abyssus">("vallum");
  const [target, setTarget] = useState(
    import.meta.env.VITE_MEETNOTE_URL || "http://127.0.0.1:8010",
  );
  const [instance, setInstance] = useState<{
    client: Client;
    info: Info;
    id: number;
  } | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [connectionError, setConnectionError] = useState("");
  const [fullBusy, setFullBusy] = useState(false);
  const [sessionBusy, setSessionBusy] = useState(false);
  const [logs, setLogs] = useState<RequestLog[]>([]);
  const connectController = useRef<AbortController | null>(null);
  const connectionGeneration = useRef(0);
  const logSequence = useRef(0);
  const current = views.find((item) => item.id === view)!;
  const busy = connecting || fullBusy || sessionBusy;
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document
      .querySelector('meta[name="theme-color"]')
      ?.setAttribute(
        "content",
        getComputedStyle(document.documentElement)
          .getPropertyValue("--bg-primary")
          .trim(),
      );
  }, [theme]);
  useEffect(() => () => connectController.current?.abort(), []);
  const report = useCallback((entry: RequestLog) => {
    const recorded = { ...entry, id: ++logSequence.current };
    setLogs((old) => [...old.slice(-59), recorded]);
  }, []);
  async function connect() {
    let normalized: string;
    try {
      normalized = normalizeTarget(target);
    } catch (e) {
      setConnectionError(errorText(e));
      return;
    }
    const controller = new AbortController();
    const generation = ++connectionGeneration.current;
    connectController.current = controller;
    setConnecting(true);
    setConnectionError("");
    setInstance(null);
    setLogs([]);
    try {
      const client = new Client(normalized, (entry) => {
        if (generation === connectionGeneration.current) report(entry);
      });
      const [health, info] = await Promise.all([
        client.request<{ status: string }>(
          "/health/live",
          "GET",
          undefined,
          controller.signal,
        ),
        client.request<Info>(
          "/api/v1/info",
          "GET",
          undefined,
          controller.signal,
        ),
      ]);
      if (
        health.status !== "ok" ||
        info.name !== "MeetNote" ||
        typeof info.model !== "string"
      )
        throw new Error(
          "目标没有返回有效的 MeetNote 实例信息，请检查服务根地址。",
        );
      setInstance({ client, info, id: generation });
      setTarget(normalized);
    } catch (e) {
      if (!controller.signal.aborted) setConnectionError(errorText(e));
      controller.abort();
    } finally {
      setConnecting(false);
      connectController.current = null;
    }
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main">
        跳转到工作区
      </a>
      <aside className="sidebar">
        <a className="brand" href="#main" aria-label="MeetNote 客户端示例">
          <span className="brand-mark" aria-hidden="true">
            m<span>n</span>
          </span>
          <span>
            <strong>MeetNote</strong>
            <small>客户端示例 / 01</small>
          </span>
        </a>
        <p className="nav-label">功能测试</p>
        <nav aria-label="功能导航">
          {views.map((item) => (
            <button
              key={item.id}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => setView(item.id)}
            >
              <item.icon size={18} strokeWidth={1.5} />
              <span>{item.label}</span>
              {view === item.id && <ArrowUpRight size={14} />}
            </button>
          ))}
        </nav>
        <div className="sidebar-note">
          <span className="eyebrow">独立客户端</span>
          <p>
            连接一个已启动的实例，
            <br />
            从输入到结果，逐项验证。
          </p>
          <div className="sidebar-rule" />
          <small>
            页面刷新后清空本地内容。
            <br />
            会话生命周期由实例管理。
          </small>
        </div>
        <div className="sidebar-bottom">
          <span>苍渊 · 白垣</span>
          <button
            className="icon-button"
            aria-label={theme === "vallum" ? "切换苍渊主题" : "切换白垣主题"}
            onClick={() =>
              setTheme((old) => (old === "vallum" ? "abyssus" : "vallum"))
            }
          >
            {theme === "vallum" ? <Moon size={17} /> : <Sun size={17} />}
          </button>
        </div>
      </aside>
      <main id="main" tabIndex={-1}>
        <header className="topbar">
          <span>
            examples <span className="slash">/</span> demo01
          </span>
          <span className="connection-state">
            <Circle size={8} fill={instance ? "currentColor" : "none"} />
            {connecting
              ? "正在检查实例"
              : instance
                ? "实例已连接"
                : "未连接实例"}
          </span>
        </header>
        <div className="page-heading">
          <div>
            <p className="eyebrow">MeetNote / 服务测试台</p>
            <h1>{current.label}</h1>
            <p>{current.description}</p>
          </div>
          <span className="edition">DEMO 01</span>
        </div>
        <section
          className="connection-panel"
          aria-labelledby="connection-title"
        >
          <div className="connection-title">
            <Plug size={17} />
            <h2 id="connection-title">服务实例</h2>
            {instance && (
              <span className="model-tag">
                <Check size={13} />
                {instance.info.model}
                {instance.info.provider === "mock" ? " · 模拟模式" : ""}
              </span>
            )}
          </div>
          <form
            className="connection-form"
            onSubmit={(event) => {
              event.preventDefault();
              void connect();
            }}
          >
            <label className="grow" htmlFor="instance-url">
              实例根地址
              <input
                id="instance-url"
                type="url"
                required
                value={target}
                disabled={busy}
                onChange={(e) => setTarget(e.target.value)}
                spellCheck={false}
                aria-describedby="connection-help"
              />
            </label>
            <button type="submit" className="secondary" disabled={busy}>
              {connecting ? "正在连接…" : instance ? "重新连接" : "连接实例"}
            </button>
          </form>
          <p id="connection-help" className="hint">
            填写 MeetNote
            服务地址，不是模型地址。重新连接会清空此页状态，服务端会话继续保留。
          </p>
          {connectionError && <Notice error>{connectionError}</Notice>}
        </section>
        {instance ? (
          <div key={instance.id} className="workspace">
            <section hidden={view !== "fulltext"} aria-label="全文生成工作区">
              <Fulltext
                client={instance.client}
                info={instance.info}
                onBusy={setFullBusy}
              />
            </section>
            <section hidden={view !== "sessions"} aria-label="流式会话工作区">
              <Sessions client={instance.client} onBusy={setSessionBusy} />
            </section>
            <section
              hidden={view !== "diagnostics"}
              aria-label="实例诊断工作区"
            >
              <Diagnostics
                client={instance.client}
                info={instance.info}
                logs={logs}
              />
            </section>
          </div>
        ) : (
          <div className="disconnected">
            <Empty
              title={
                connecting ? "正在检查服务连接" : "先连接一个 MeetNote 实例"
              }
            >
              在上方填写已启动实例的根地址。连接后即可提交原文、创建会话和检查服务接口。
            </Empty>
            <div className="start-guide">
              <span>
                01 <strong>连接实例</strong>
              </span>
              <span>
                02 <strong>提交会议原文</strong>
              </span>
              <span>
                03 <strong>检查真实结果</strong>
              </span>
            </div>
          </div>
        )}
        <footer className="page-footer">
          <span>仅在内存中处理会议内容</span>
          <span>MANAS × ABYSSUS · VALLUM</span>
        </footer>
      </main>
    </div>
  );
}
