import { useEffect, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import { Check, Clipboard, FileText } from "lucide-react";
import { Client, errorText } from "./api";
import type { Sample, Transcript } from "./types";

export function Notice({
  children,
  error = false,
}: {
  children: ReactNode;
  error?: boolean;
}) {
  return (
    <div
      className={`notice${error ? " error" : ""}`}
      role={error ? "alert" : "status"}
    >
      {children}
    </div>
  );
}
export function Empty({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <FileText size={28} strokeWidth={1.3} aria-hidden="true" />
      <h3>{title}</h3>
      <p>{children}</p>
    </div>
  );
}
export function Json({
  value,
  label = "原始响应",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="raw">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}
export function Document({ text }: { text: string }) {
  return (
    <div className="document">
      <ReactMarkdown
        skipHtml
        components={{ img: ({ alt }) => <span>{alt || "图片已省略"}</span> }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
export function Copy({ text }: { text: string }) {
  const [message, setMessage] = useState("");
  useEffect(() => {
    if (!message) return;
    const timer = setTimeout(() => setMessage(""), 2000);
    return () => clearTimeout(timer);
  }, [message]);
  return (
    <span className="copy-control">
      <button
        type="button"
        className="quiet small"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(text);
            setMessage("已复制");
          } catch {
            setMessage("复制失败，请手动选择正文");
          }
        }}
      >
        {message === "已复制" ? <Check size={15} /> : <Clipboard size={15} />}
        复制
      </button>
      <span role="status">{message}</span>
    </span>
  );
}
export function SamplePicker({
  client,
  disabled,
  onLoad,
}: {
  client: Client;
  disabled?: boolean;
  onLoad: (sample: Transcript) => void;
}) {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [id, setId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    client
      .request<Sample[]>("/api/v1/samples", "GET", undefined, controller.signal)
      .then(setSamples)
      .catch((e) => {
        if (!controller.signal.aborted) setError(errorText(e));
      });
    return () => controller.abort();
  }, [client, reload]);
  return (
    <div className="sample-picker">
      <div className="inline">
        <label className="grow">
          服务端样本
          <select
            value={id}
            disabled={disabled || busy}
            onChange={(e) => setId(e.target.value)}
          >
            <option value="">选择会议样本</option>
            {samples.map((sample) => (
              <option key={sample.id} value={sample.id}>
                {sample.title} · {sample.variant} · {sample.characters} 字
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary"
          disabled={!id || disabled || busy}
          onClick={async () => {
            setBusy(true);
            setError("");
            try {
              onLoad(
                await client.request<Transcript>(
                  "/api/v1/samples/" + encodeURIComponent(id),
                ),
              );
            } catch (e) {
              setError(errorText(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "正在载入…" : "载入样本"}
        </button>
      </div>
      {error && (
        <div className="inline">
          <p className="field-error" role="alert">
            样本读取失败：{error}
          </p>
          <button
            className="quiet small"
            onClick={() => {
              setError("");
              setReload((n) => n + 1);
            }}
          >
            重试样本列表
          </button>
        </div>
      )}
    </div>
  );
}
