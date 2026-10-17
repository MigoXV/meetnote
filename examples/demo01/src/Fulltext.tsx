import { useEffect, useRef, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpRight, Square } from "lucide-react";
import { ApiError, Client, errorText } from "./api";
import { Copy, Document, Empty, Json, Notice, SamplePicker } from "./ui";
import type { Capability, FulltextResult, Info } from "./types";

const labels: Record<Capability, string> = {
  abstract: "会议摘要",
  minutes: "会议纪要",
  actions: "工作计划",
  polish: "全文润色",
  keywords: "关键词",
};
const choices: Capability[] = [
  "abstract",
  "minutes",
  "actions",
  "polish",
  "keywords",
];
const defaults: Capability[] = ["abstract", "minutes", "keywords", "actions"];
function products(result: FulltextResult) {
  const values: { id: string; name: string; text: string }[] = [];
  if (result.final_abstract !== undefined)
    values.push({
      id: "abstract",
      name: labels.abstract,
      text: result.final_abstract,
    });
  if (result.minutes !== undefined)
    values.push({
      id: "minutes",
      name: labels.minutes,
      text: result.minutes.markdown,
    });
  if (result.next_actions !== undefined)
    values.push({
      id: "actions",
      name: labels.actions,
      text: result.next_actions.markdown,
    });
  if (result.polished_text !== undefined)
    values.push({
      id: "polish",
      name: labels.polish,
      text: result.polished_text,
    });
  if ("keywords" in result)
    values.push({
      id: "keywords",
      name: labels.keywords,
      text: result.keywords ?? "关键词能力当前为占位，服务返回 null。",
    });
  return values;
}

export default function Fulltext({
  client,
  info,
  onBusy,
}: {
  client: Client;
  info: Info;
  onBusy: (busy: boolean) => void;
}) {
  const [text, setText] = useState("");
  const [id, setId] = useState(() => "demo-" + crypto.randomUUID());
  const [pipeline, setPipeline] = useState<Capability[]>(defaults);
  const [result, setResult] = useState<FulltextResult | null>(null);
  const [state, setState] = useState<
    "idle" | "running" | "done" | "failed" | "cancelled"
  >("idle");
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  const [tab, setTab] = useState("minutes");
  const controller = useRef<AbortController | null>(null);
  const running = state === "running";
  const available = !!info.capabilities?.includes("fulltext_generate");
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!running) return;
    const start = Date.now();
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [running]);
  const items = result ? products(result) : [];
  const active = items.find((item) => item.id === tab) ?? items[0];
  async function generate() {
    if (!/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(id.trim())) {
      setError(
        "请求 ID 为 1～128 位，首位使用字母或数字，仅允许字母、数字、点、下划线、冒号和连字符。",
      );
      return;
    }
    const current = new AbortController();
    controller.current = current;
    setState("running");
    setResult(null);
    setError("");
    setElapsed(0);
    onBusy(true);
    const start = Date.now();
    try {
      const data = await client.request<FulltextResult>(
        "/v1/fulltext/generate",
        "POST",
        { id: id.trim(), text, pipeline },
        current.signal,
      );
      setResult(data);
      setState(data.message ? "failed" : "done");
    } catch (e) {
      if (current.signal.aborted) setState("cancelled");
      else {
        setState("failed");
        setError(errorText(e));
        if (
          e instanceof ApiError &&
          e.payload &&
          typeof e.payload === "object" &&
          "id" in e.payload
        )
          setResult(e.payload as FulltextResult);
      }
    } finally {
      setElapsed(Math.floor((Date.now() - start) / 1000));
      onBusy(false);
      controller.current = null;
    }
  }
  function move(index: number, offset: number) {
    setPipeline((previous) => {
      const next = [...previous];
      [next[index], next[index + offset]] = [next[index + offset], next[index]];
      return next;
    });
  }
  return (
    <>
      {!available && (
        <Notice>
          当前实例未声明全文生成能力。请连接包含 <code>fulltext_generate</code>{" "}
          的新版实例；会话与诊断仍可使用。
        </Notice>
      )}
      <div className="workbench">
        <section className="input-pane" aria-labelledby="input-title">
          <div className="section-heading">
            <h2 id="input-title">会议原文</h2>
            <span>01 / 输入</span>
          </div>
          <SamplePicker
            client={client}
            disabled={running}
            onLoad={(sample) =>
              setText(
                sample.segments
                  .map((s) => `${s.speaker ? s.speaker + "：" : ""}${s.text}`)
                  .join("\n"),
              )
            }
          />
          <label className="text-label" htmlFor="fulltext">
            完整转写<span>{text.length.toLocaleString()} / 60,000 字符</span>
          </label>
          <textarea
            id="fulltext"
            className="transcript-input"
            value={text}
            onChange={(e) => setText(e.target.value)}
            disabled={running}
            placeholder="粘贴一场会议的完整转写，或从实例载入样本。"
          />
          <p className="hint">
            有效输入至少 80 字符、含 20 个中文字符。每项能力独立读取原文。
          </p>
          <label>
            请求 ID
            <input
              value={id}
              maxLength={128}
              disabled={running}
              onChange={(e) => setId(e.target.value)}
            />
          </label>
          <fieldset disabled={running}>
            <legend>生成能力</legend>
            <div className="capabilities">
              {choices.map((name) => (
                <label className="check" key={name}>
                  <input
                    type="checkbox"
                    checked={pipeline.includes(name)}
                    onChange={(e) =>
                      setPipeline((old) =>
                        e.target.checked
                          ? [...old, name]
                          : old.filter((n) => n !== name),
                      )
                    }
                  />
                  <span>
                    {labels[name]}
                    {name === "keywords" && <small>占位</small>}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <div className="section-heading compact">
            <h3>执行顺序</h3>
            <button
              className="quiet small"
              disabled={running}
              onClick={() => setPipeline(defaults)}
            >
              恢复默认
            </button>
          </div>
          <ol className="pipeline">
            {pipeline.map((name, index) => (
              <li key={name}>
                <span className="step-number">{index + 1}</span>
                <span>{labels[name]}</span>
                <div className="inline">
                  <button
                    className="icon-button small"
                    aria-label={`上移${labels[name]}`}
                    disabled={running || index === 0}
                    onClick={() => move(index, -1)}
                  >
                    <ArrowUp size={14} />
                  </button>
                  <button
                    className="icon-button small"
                    aria-label={`下移${labels[name]}`}
                    disabled={running || index === pipeline.length - 1}
                    onClick={() => move(index, 1)}
                  >
                    <ArrowDown size={14} />
                  </button>
                </div>
              </li>
            ))}
          </ol>
          {!pipeline.length && (
            <p className="field-error">至少选择一项能力。</p>
          )}
          {error && <Notice error>{error}</Notice>}
          <div className="action-row generation-actions">
            <button
              className="primary"
              disabled={
                !available ||
                running ||
                !text.trim() ||
                text.trim().length > 60000 ||
                !pipeline.length
              }
              onClick={() => void generate()}
            >
              <ArrowUpRight size={17} />
              {running ? "正在生成…" : "生成所选内容"}
            </button>
            {running && (
              <button
                className="secondary"
                onClick={() => controller.current?.abort()}
              >
                <Square size={14} />
                取消请求
              </button>
            )}
          </div>
        </section>
        <section
          className="output-pane"
          aria-labelledby="output-title"
          aria-busy={running}
        >
          <div className="section-heading">
            <h2 id="output-title">生成结果</h2>
            <span role="status">
              {state === "idle"
                ? "等待请求"
                : state === "running"
                  ? `处理中 · ${elapsed} 秒`
                  : state === "done"
                    ? `已返回 · ${elapsed} 秒`
                    : state === "cancelled"
                      ? "已取消请求"
                      : "存在失败提示"}
            </span>
          </div>
          {running && (
            <Notice>
              正在等待实例完成全部所选能力。此接口一次返回最终结果，请保持连接。
            </Notice>
          )}
          {result?.message && <Notice error>{result.message}</Notice>}
          {active ? (
            <>
              <div className="tabs" role="group" aria-label="生成产物">
                {items.map((item) => (
                  <button
                    aria-pressed={active.id === item.id}
                    key={item.id}
                    onClick={() => setTab(item.id)}
                  >
                    {item.name}
                  </button>
                ))}
              </div>
              <div className="document-toolbar">
                <span>{active.name}</span>
                <Copy text={active.text} />
              </div>
              <Document text={active.text} />
              <Json value={result} />
            </>
          ) : (
            <Empty
              title={
                running
                  ? "内容正在整理"
                  : state === "cancelled"
                    ? "请求已中断"
                    : "让结果从原文中产生"
              }
            >
              {running
                ? "这里只展示实例真实返回的内容。"
                : "选择生成能力并提交原文，摘要、纪要和工作计划将显示在这里。"}
            </Empty>
          )}
        </section>
      </div>
    </>
  );
}
