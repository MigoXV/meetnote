import { test } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { once } from "node:events";
import { bridge } from "../proxy.mjs";

async function serve(t, handler) {
  const server = http.createServer(handler);
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  t.after(() => {
    server.closeAllConnections();
    server.close();
  });
  return `http://127.0.0.1:${server.address().port}`;
}
async function gateway(t) {
  return serve(t, (req, res) =>
    bridge(req, res, () => {
      res.writeHead(404);
      res.end();
    }),
  );
}

test("代理只转发公开接口，拒绝无效实例地址", async (t) => {
  const base = await gateway(t);
  for (const target of [
    "file:///etc/passwd",
    "http://user:pass@localhost",
    "http://localhost?a=b",
    "not a url",
  ]) {
    assert.equal(
      (
        await fetch(base + "/bridge/health/live", {
          headers: { "X-MeetNote-Target": target },
        })
      ).status,
      400,
    );
  }
  assert.equal(
    (
      await fetch(base + "/bridge/private", {
        headers: { "X-MeetNote-Target": "http://127.0.0.1" },
      })
    ).status,
    404,
  );
});

test("保持请求正文、路径和状态，禁止转发客户端 cookie 和目标头", async (t) => {
  let received;
  const upstream = await serve(t, async (req, res) => {
    let body = "";
    for await (const chunk of req) body += chunk;
    received = {
      body: JSON.parse(body),
      url: req.url,
      method: req.method,
      headers: req.headers,
    };
    res.writeHead(202, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ duplicate: false }));
  });
  const base = await gateway(t);
  const response = await fetch(base + "/bridge/api/v1/sessions/s1/chunks", {
    method: "POST",
    headers: {
      "X-MeetNote-Target": upstream + "/prefix",
      "Content-Type": "application/json",
      Cookie: "secret=value",
    },
    body: JSON.stringify({ text: "中文不能丢失", chunk_id: "one" }),
  });
  assert.equal(response.status, 202);
  assert.deepEqual(received.body, { text: "中文不能丢失", chunk_id: "one" });
  assert.equal(received.url, "/prefix/api/v1/sessions/s1/chunks");
  assert.equal(received.method, "POST");
  assert.equal(received.headers.cookie, undefined);
  assert.equal(received.headers["x-meetnote-target"], undefined);
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("SSE 不缓冲并传递 Last-Event-ID", async (t) => {
  let cursor;
  let finish;
  const upstream = await serve(t, (req, res) => {
    cursor = req.headers["last-event-id"];
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write('id: 8\ndata: {"type":"first"}\n\n');
    finish = () => res.end('id: 9\ndata: {"type":"last"}\n\n');
  });
  const base = await gateway(t);
  const response = await fetch(base + "/bridge/api/v1/sessions/s1/events", {
    headers: { "X-MeetNote-Target": upstream, "Last-Event-ID": "7" },
  });
  const reader = response.body.getReader();
  const first = await reader.read();
  assert.match(new TextDecoder().decode(first.value), /"first"/);
  assert.equal(cursor, "7");
  finish();
  await reader.cancel();
});

test("浏览器断连会关闭上游 SSE", async (t) => {
  let closed;
  const finished = new Promise((resolve) => {
    closed = resolve;
  });
  const upstream = await serve(t, (_req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.write(": connected\n\n");
    res.on("close", closed);
  });
  const base = await gateway(t);
  const controller = new AbortController();
  const response = await fetch(base + "/bridge/api/v1/sessions/s1/events", {
    signal: controller.signal,
    headers: { "X-MeetNote-Target": upstream },
  });
  await response.body.getReader().read();
  controller.abort();
  await Promise.race([
    finished,
    new Promise((_, reject) => {
      const timer = setTimeout(
        () => reject(new Error("upstream was not cancelled")),
        1000,
      );
      timer.unref();
    }),
  ]);
});

test("无法连接实例时返回安全的 502 提示", async (t) => {
  const base = await gateway(t);
  const response = await fetch(base + "/bridge/health/live", {
    headers: { "X-MeetNote-Target": "http://127.0.0.1:1" },
  });
  assert.equal(response.status, 502);
  assert.match((await response.json()).detail, /无法连接/);
});
