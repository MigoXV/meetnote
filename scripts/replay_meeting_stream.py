"""只读应用逐字稿，通过原 SummarySession 回放；内容、事件和产物仅保留在内存。"""

import argparse
import asyncio
import hashlib
import importlib.util
import json
import re
from collections import Counter
from pathlib import Path
from time import monotonic
from types import SimpleNamespace

import httpx


def report(kind, **data):
    print(json.dumps({"kind": kind, **data}, ensure_ascii=False), flush=True)


async def read_snapshot(client, url, cached=None):
    response = await client.get(url)
    if response.status_code == 404 and cached and cached.get("status") == "completed":
        return cached
    response.raise_for_status()
    return response.json()


class MemoryRuntime:
    def __init__(self, client, base, timeout):
        self.llm = SimpleNamespace(client=client, slots=asyncio.Semaphore(1))
        self.settings = SimpleNamespace(summary_stream_url=base, engine_timeout=timeout)
        self.products = {}
        self.updates = Counter()

    async def product(self, mid, key, state, message, content=None):
        products = self.products.setdefault(mid, {})
        products[key] = {**products.get(key, {}), "state": state}
        if content is not None:
            products[key]["text"] = content
        self.updates[key + "." + state] += 1

    async def publish(self, mid, event):
        self.updates[event.get("CMD", "warning")] += 1


async def replay(args):
    spec = importlib.util.spec_from_file_location(
        "meeting_summary_adapter", args.application_source / "src/meeting/adapters/summary.py"
    )
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    async with httpx.AsyncClient(
        timeout=args.timeout,
        trust_env=False,
        limits=httpx.Limits(max_keepalive_connections=0),
    ) as client:
        ready = await client.get(args.summary_base + "/health/ready")
        ready.raise_for_status()
        credentials = json.loads(args.credentials.read_text())
        login = await client.post(
            args.application_base + "/auth/login",
            data={k: credentials[k] for k in ("username", "password")},
        )
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["result"]["access-token"]}
        response = await client.get(
            args.application_base + "/meeting/records",
            params={"meetingId": args.meeting_id},
            headers=headers,
        )
        response.raise_for_status()
        rows = sorted(response.json()["result"], key=lambda r: (r.get("startTime") or 0, r["id"]))
        rows = [
            r for r in rows if (r.get("rawTransferText") or r.get("transferText") or "").strip()
        ]
        if not rows:
            raise ValueError("会议没有逐字稿")
        texts = [r.get("rawTransferText") or r["transferText"] for r in rows]
        report(
            "input",
            meeting_id=args.meeting_id,
            records=len(rows),
            speed=args.speed,
            characters=sum(map(len, texts)),
            sha256=hashlib.sha256("".join(texts).encode()).hexdigest(),
        )
        runtime = MemoryRuntime(client, args.summary_base, args.timeout)
        session = adapter.SummarySession(runtime, {"id": args.meeting_id, "title": "流式回归验证"})
        counts, repeated, requests = Counter(), Counter(), Counter()
        rounds, prior_sizes = {}, {}
        last_snapshot = {}
        started = monotonic()

        async def observe():
            while session.sid is None:
                if session.error:
                    raise session.error
                await asyncio.sleep(0.05)
            async with client.stream(
                "GET", args.summary_base + f"/api/v1/sessions/{session.sid}/events"
            ) as stream:
                stream.raise_for_status()
                async for line in stream.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[5:])
                    kind, data = event["type"], event.get("data", {})
                    counts[kind] += 1
                    if kind == "session.snapshot" and data.get("status"):
                        last_snapshot["state"] = data
                    elif kind == "model.started":
                        task = data.get("task", "")
                        digest = hashlib.sha256(
                            json.dumps(data.get("input"), sort_keys=True).encode()
                        ).hexdigest()
                        repeated[task + ":" + digest] += 1
                        requests[task] += 1
                    elif kind == "model.validation_failed":
                        report("validation", task=data.get("task"), error=data.get("error"))
                    elif kind == "minutes.delta":
                        rnd = data.get("round")
                        size = sum(len(v) for v in data.get("blocks", {}).values())
                        if size < prior_sizes.get(rnd, 0):
                            counts["preview.shrank"] += 1
                        prior_sizes[rnd] = size
                    elif kind == "minutes.updated":
                        draft = data.get("minutes", {})
                        rounds[data.get("round")] = len(draft.get("markdown", ""))
                        report(
                            "draft",
                            elapsed=round(monotonic() - started, 1),
                            round=data.get("round"),
                            chars=rounds[data.get("round")],
                            consumed=data.get("covered_chars"),
                            selected_topics=[s["title"] for s in draft.get("sections", [])],
                            culture_in_ledger=any(
                                re.search("作风|文化|精神面貌|居安思危", f["text"])
                                for f in draft.get("checklist", [])
                            ),
                            culture_in_minutes=bool(
                                re.search("作风|文化|精神面貌|居安思危", draft.get("markdown", ""))
                            ),
                        )
                    elif kind == "minutes.aborted":
                        report("draft_failed", error=data.get("error"))

        async def progress():
            while True:
                await asyncio.sleep(30)
                if not session.sid:
                    continue
                state = await read_snapshot(
                    client, args.summary_base + f"/api/v1/sessions/{session.sid}",
                    last_snapshot.get("state"),
                )
                last_snapshot["state"] = state
                report(
                    "progress",
                    elapsed=round(monotonic() - started),
                    status=state.get("status"),
                    accepted=state.get("accepted_count"),
                    remaining=state.get("remaining_chars"),
                    facts=len(state.get("facts", [])),
                    requests=dict(requests),
                )

        observer, ticker = asyncio.create_task(observe()), asyncio.create_task(progress())
        try:
            origin = rows[0].get("endTime") or 0
            for index, (row, text) in enumerate(zip(rows, texts)):
                target = ((row.get("endTime") or 0) - origin) / 1000 / args.speed
                await asyncio.sleep(max(0, target - (monotonic() - started)))
                session.append(
                    {
                        **row,
                        "rawTransferText": text,
                        "segmentID": row.get("segmentID") or str(row["id"]),
                    }
                )
                if session.error:
                    raise session.error
            result = await asyncio.wait_for(session.finish(), args.timeout)
            state = await read_snapshot(
                client, args.summary_base + f"/api/v1/sessions/{session.sid}",
                last_snapshot.get("state"),
            )
            draft = state.get("minutes") or {}
            facts = {f["id"]: f for f in state.get("facts", [])}
            narrative = draft.get("narrative") or {}
            selected = set()
            for value in narrative.values():
                for paragraph in value if isinstance(value, list) else [value]:
                    selected.update(paragraph.get("fact_ids", []))
            minutes = result["minutes"]["markdown"]
            tags = {
                "模型研发": r"数据|模型|推理",
                "Harness": r"Harness|上下文|框架",
                "算力配置": r"服务器|算力|硬件|卡",
                "作风文化": r"作风|文化|精神面貌|居安思危",
            }
            report(
                "result",
                elapsed=round(monotonic() - started, 1),
                status=state["status"],
                remaining=state.get("remaining_chars"),
                stats=state.get("stats"),
                events=dict(counts),
                repeated_inputs=sum(n - 1 for n in repeated.values()),
                product_updates=dict(runtime.updates),
                coverage=draft.get("coverage"),
                topics=dict(Counter(f["topic"] for f in facts.values())),
                narrative_topics=sorted({facts[f]["topic"] for f in selected if f in facts}),
                product_characters={
                    k: len(v.get("text", "")) for k, v in runtime.products[args.meeting_id].items()
                },
                minutes_tags={k: bool(re.search(v, minutes, re.I)) for k, v in tags.items()},
                minutes=minutes,
                summary=result.get("final_abstract"),
                actions=(result.get("next_actions") or {}).get("markdown"),
            )
        finally:
            for task in (observer, ticker):
                task.cancel()
            await asyncio.gather(observer, ticker, return_exceptions=True)
            await session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meeting-id", type=int, default=266)
    parser.add_argument("--application-base", default="http://127.0.0.1:18095/meetingsystem")
    parser.add_argument("--summary-base", default="http://127.0.0.1:8010")
    parser.add_argument(
        "--application-source", type=Path, default=Path("/home/meeting-python/repository")
    )
    parser.add_argument(
        "--credentials",
        type=Path,
        default=Path("/home/meeting-dev/config/application-credentials.json"),
    )
    parser.add_argument("--speed", type=float, default=6)
    parser.add_argument("--timeout", type=float, default=1800)
    arguments = parser.parse_args()
    if arguments.speed <= 0:
        parser.error("speed 必须大于 0")
    asyncio.run(replay(arguments))
