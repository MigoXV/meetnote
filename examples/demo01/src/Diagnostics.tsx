import { useState } from "react";
import { ExternalLink, RefreshCw } from "lucide-react";
import { Client, errorText } from "./api";
import { Json, Notice } from "./ui";
import type { Info, RequestLog } from "./types";

const endpoints = [
  ["GET", "/health/live", "服务存活"],
  ["GET", "/api/v1/info", "模型、版本与能力"],
  ["POST", "/v1/fulltext/generate", "全文同步生成"],
  ["GET / POST", "/api/v1/sessions", "会话列表 / 创建"],
  ["GET / DELETE", "/api/v1/sessions/{id}", "快照 / 释放"],
  ["POST", "/api/v1/sessions/{id}/chunks", "追加定稿片段"],
  ["POST", "/api/v1/sessions/{id}/finalize", "结束输入并收尾"],
  ["GET", "/api/v1/sessions/{id}/events", "SSE 订阅与游标补发"],
  ["GET", "/api/v1/samples", "服务端样本列表"],
  ["GET", "/api/v1/samples/{id}", "样本原文详情"],
];

export default function Diagnostics({
  client,
  info,
  logs,
}: {
  client: Client;
  info: Info;
  logs: RequestLog[];
}) {
  const [check, setCheck] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [schema, setSchema] = useState<unknown>(null);
  const [health, setHealth] = useState<unknown>(null);
  return (
    <div className="diagnostics">
      <section aria-labelledby="instance-title">
        <div className="section-heading">
          <h2 id="instance-title">实例信息</h2>
          <a
            className="quiet"
            href={client.target + "/docs"}
            target="_blank"
            rel="noreferrer"
          >
            打开接口文档
            <ExternalLink size={14} />
          </a>
        </div>
        <dl className="instance-details">
          <div>
            <dt>服务地址</dt>
            <dd className="mono">{client.target}</dd>
          </div>
          <div>
            <dt>模型</dt>
            <dd>{info.model}</dd>
          </div>
          <div>
            <dt>模型接入</dt>
            <dd>
              {info.provider}
              {info.provider === "mock" ? " · 模拟实例，不能验证模型质量" : ""}
            </dd>
          </div>
          <div>
            <dt>快照契约</dt>
            <dd>v{info.contract_version}</dd>
          </div>
          <div>
            <dt>终态保留</dt>
            <dd>{info.retention_seconds} 秒</dd>
          </div>
          <div>
            <dt>持久化</dt>
            <dd>{info.persistence ? "实例报告已启用" : "实例报告未启用"}</dd>
          </div>
        </dl>
        <div className="action-row">
          <button
            className="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              setCheck("");
              try {
                const data = await client.request<{ status: string }>(
                  "/health",
                );
                setHealth(data);
                setCheck(`健康检查已返回：${data.status}`);
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            <RefreshCw size={15} />
            健康检查
          </button>
          <button
            className="secondary"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                setSchema(await client.request("/openapi.json"));
                setCheck("已读取当前实例的 OpenAPI 定义。");
              } catch (e) {
                setError(errorText(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            读取 OpenAPI
          </button>
        </div>
        {check && <Notice>{check}</Notice>}
        {error && <Notice error>{error}</Notice>}
        {health !== null && <Json value={health} label="健康检查响应" />}
        <Json value={info} label="实例信息原始响应" />
        {schema !== null && <Json value={schema} label="OpenAPI 原始响应" />}
      </section>
      <section aria-labelledby="endpoint-title">
        <div className="section-heading">
          <h2 id="endpoint-title">功能与接口</h2>
          <span>请求由本地客户端转发</span>
        </div>
        <div className="endpoint-list">
          {endpoints.map(([method, path, name]) => (
            <div key={path}>
              <span className="method mono">{method}</span>
              <code>{path}</code>
              <span>{name}</span>
            </div>
          ))}
        </div>
      </section>
      <section aria-labelledby="request-title">
        <div className="section-heading">
          <h2 id="request-title">最近请求</h2>
          <span>最近 60 次 · 仅当前页面内存</span>
        </div>
        {logs.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>方法与路径</th>
                  <th>响应</th>
                  <th>耗时</th>
                </tr>
              </thead>
              <tbody>
                {logs
                  .slice()
                  .reverse()
                  .map((log) => (
                    <tr key={log.id}>
                      <td>
                        <span className="method mono">{log.method}</span>
                        <code>{log.path}</code>
                      </td>
                      <td>{log.status}</td>
                      <td>{log.ms.toLocaleString()} ms</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="hint">
            连接实例后，请求信息会显示在这里。正文不进入请求记录。
          </p>
        )}
        <p className="hint">
          SSE 耗时为建立连接的时间；全文请求耗时包含实例排队与生成时间。
        </p>
      </section>
    </div>
  );
}
