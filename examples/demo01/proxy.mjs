import http from "node:http";
import https from "node:https";

// 仅转发请求，不导入 MeetNote、不启动模型，也不缓存会议数据。
export function bridge(req, res, next) {
  if (!req.url?.startsWith("/bridge/")) return next();
  const fail = (status, detail) => {
    if (res.destroyed) return;
    if (res.headersSent) return res.destroy();
    res.writeHead(status, {
      "Content-Type": "application/json; charset=utf-8",
    });
    res.end(JSON.stringify({ detail }));
  };
  let destination;
  try {
    const target = new URL(String(req.headers["x-meetnote-target"] || ""));
    if (
      !["http:", "https:"].includes(target.protocol) ||
      target.username ||
      target.password ||
      target.search ||
      target.hash
    ) {
      throw new Error("invalid target");
    }
    const path = req.url.slice("/bridge".length);
    if (
      !/^\/(?:health(?:\/live)?|api\/v1(?:\/[^?]*)?|v1\/fulltext\/generate|openapi\.json)(?:\?.*)?$/.test(
        path,
      )
    ) {
      return fail(404, "此转发入口仅支持 MeetNote 公开接口。");
    }
    destination = new URL(
      target.origin + target.pathname.replace(/\/$/, "") + path,
    );
  } catch {
    return fail(400, "实例地址必须是有效的 HTTP 或 HTTPS 服务根地址。");
  }
  const headers = {};
  for (const name of ["content-type", "accept", "last-event-id"]) {
    if (req.headers[name]) headers[name] = req.headers[name];
  }
  const upstream = (destination.protocol === "https:" ? https : http).request(
    destination,
    { method: req.method, headers },
    (incoming) => {
      res.writeHead(incoming.statusCode || 502, {
        "Content-Type": incoming.headers["content-type"] || "application/json",
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
      });
      res.flushHeaders();
      incoming.on("error", () => res.destroy());
      incoming.pipe(res);
    },
  );
  upstream.setTimeout(0); // 全文长请求和 SSE 不设置短读取超时。
  const connectTimer = setTimeout(() => {
    fail(504, "连接实例超时，请检查地址、端口和网络后重试。");
    upstream.destroy();
  }, 10000);
  upstream.on("socket", (socket) => {
    if (!socket.connecting) clearTimeout(connectTimer);
    else
      socket.once(
        destination.protocol === "https:" ? "secureConnect" : "connect",
        () => clearTimeout(connectTimer),
      );
  });
  upstream.on("error", () => {
    clearTimeout(connectTimer);
    fail(502, "无法连接 MeetNote 实例，请检查服务是否启动及地址是否正确。");
  });
  res.on("close", () => {
    clearTimeout(connectTimer);
    upstream.destroy(); // 浏览器取消时同步关闭上游请求，保留取消语义。
  });
  req.on("error", () => upstream.destroy());
  req.pipe(upstream);
}
