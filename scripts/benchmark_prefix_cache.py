"""用合成台账测量真实 Responses 前缀缓存；报告只含数值、配置和哈希。"""

import argparse
import asyncio
import hashlib
import json
import re
import statistics
import subprocess
import uuid
from importlib.resources import files
from pathlib import Path
from time import perf_counter

import httpx
from tokenizers import Tokenizer

from meetnote.domain.results import extraction_schema, reader_minutes_schema
from meetnote.llm.client import strict_schema

INSTRUCTIONS = (
    "你正在做合成性能测试。阅读下面的虚构台账，写一篇详细的中文分析，"
    "完整说明所有条目的条件、状态和注意事项，至少五百字。只输出正文。"
)


def schema_prefix_analysis(tokenizer):
    values = []
    for task, left, right in [
        ("extract", extraction_schema(["s1.0", "s2.0"]), extraction_schema(["s3.0", "s4.0"])),
        (
            "minutes",
            reader_minutes_schema([f"f{i:06d}" for i in range(18)]),
            reader_minutes_schema([f"f{i:06d}" for i in range(19)]),
        ),
    ]:
        prefix = files("meetnote.prompts").joinpath(task + ".txt").read_text()
        prefix += "\n\nReturn JSON matching this schema and its field descriptions:\n"
        tokens = [
            tokenizer.encode(
                prefix
                + json.dumps(strict_schema(schema), ensure_ascii=False, separators=(",", ":")),
                add_special_tokens=False,
            ).ids
            for schema in (left, right)
        ]
        common = 0
        for a, b in zip(*tokens):
            if a != b:
                break
            common += 1
        values.append(
            {
                "task": task,
                "shared_system_body_tokens": common,
                "system_body_tokens": [len(t) for t in tokens],
            }
        )
    return values


def synthetic_body(tokenizer, tokens):
    rows = []
    while len(tokenizer.encode("\n".join(rows)).ids) < tokens:
        offset = len(rows)
        rows.extend(
            f"条目{i:05d}：虚构项目的预算上限为{i % 97 + 3}万元；"
            "当前方案尚待验收，只有测试通过后才能发布；负责人和日期未定。"
            for i in range(offset, offset + 256)
        )
    ids = tokenizer.encode("\n".join(rows), add_special_tokens=False).ids[:tokens]
    return tokenizer.decode(ids)


def llm_memory():
    result = subprocess.run(["npu-smi", "info"], capture_output=True, text=True, check=True)
    match = re.search(
        r"\|\s*\d+\s+\d+\s*\|\s*(\d+)\s*\|\s*VLLMEngineCor\s*\|\s*(\d+)",
        result.stdout,
    )
    return {"pid": int(match[1]), "memory_mb": int(match[2])} if match else None


async def request(client, base, model, text, output_tokens):
    started = perf_counter()
    first = None
    digest = hashlib.sha256()
    final = None
    async with client.stream(
        "POST",
        base + "/v1/responses",
        json={
            "model": model,
            "instructions": INSTRUCTIONS,
            "input": text,
            "stream": True,
            "store": False,
            "temperature": 0,
            "seed": 0,
            "enable_thinking": False,
            "max_output_tokens": output_tokens,
        },
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            event = json.loads(line[5:])
            if event["type"] == "response.output_text.delta" and event.get("delta"):
                if first is None:
                    first = perf_counter()
                digest.update(event["delta"].encode())
            elif event["type"] in {"response.completed", "response.incomplete"}:
                final = event["response"]
            elif event["type"] in {"response.failed", "error"}:
                raise RuntimeError("benchmark generation failed")
    ended = perf_counter()
    if final is None or first is None:
        raise RuntimeError("benchmark stream has no confirmed terminal response")
    usage = final["usage"]
    count = usage["output_tokens"]
    return {
        "response_id": final["id"],
        "status": final["status"],
        "input_tokens": usage["input_tokens"],
        "output_tokens": count,
        "cached_tokens": (usage.get("input_tokens_details") or {}).get("cached_tokens", 0),
        "ttft_ms": round((first - started) * 1000, 3),
        "total_ms": round((ended - started) * 1000, 3),
        "post_first_delta_tokens_per_second": round((count - 1) / max(ended - first, 1e-6), 3),
        "output_sha256": digest.hexdigest(),
    }


def summarize(rows):
    result = []
    for size in sorted({r["size"] for r in rows}):
        group = [r for r in rows if r["size"] == size]
        summary = {"size": size, "phases": {}}
        for phase in dict.fromkeys(r["phase"] for r in group):
            values = [r for r in group if r["phase"] == phase and r.get("revision", 1) > 0]
            if not values:
                continue
            summary["phases"][phase] = {
                key: round(statistics.median(r[key] for r in values), 3)
                for key in ("ttft_ms", "total_ms", "input_tokens", "output_tokens", "cached_tokens")
            }
        result.append(summary)
    return result


async def benchmark(args):
    async with httpx.AsyncClient(timeout=600, trust_env=False) as client:
        ready = (await client.get(args.base + "/health/ready")).json()
        if ready.get("status") != "ready" or not ready.get("prefix_caching"):
            raise RuntimeError("requested model is not ready with prefix caching")
        tokenizer = Tokenizer.from_file(
            str(Path(ready["runtime"]["tokenizer_path"]) / "tokenizer.json")
        )
        if args.analyze_schema:
            analysis = {
                "model": ready["model"],
                "schema_prefix_analysis": schema_prefix_analysis(tokenizer),
            }
            if args.output:
                args.output.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(analysis, ensure_ascii=False), flush=True)
            return
        report = {
            "model": ready["model"],
            "instance_id": ready["instance_id"],
            "configuration": {
                k: ready[k]
                for k in (
                    "max_concurrency",
                    "max_input_tokens",
                    "max_output_tokens",
                    "kv_cache_bytes",
                    "batch_tokens",
                    "graph_mode",
                    "speculative_tokens",
                    "prefix_caching",
                )
            },
            "packages": ready["runtime"]["packages"],
            "memory_before": llm_memory(),
            "rounds": args.rounds,
            "layout_experiment": args.layout,
            "rows": [],
            "notes": "合成台账，串行配对；miss 使用新前缀，repeat 重复完整输入，changed_tail 只改末尾问题。输出上限固定，允许长度终止。",
        }
        await request(client, args.base, ready["model"], "合成测试预热：请开始详细说明。", 16)
        for round_index in range(args.rounds):
            for size in args.sizes:
                data = synthetic_body(tokenizer, size)
                if args.layout:
                    for phase in ("volatile_prefix", "stable_prefix"):
                        nonce = uuid.uuid4().hex
                        evidence = "合成会话 " + nonce + "\n" + data
                        for revision in range(3):
                            changes = {
                                "revision": nonce + str(revision),
                                "question": "请分析所有条目。",
                            }
                            payload = (
                                {**changes, "evidence": evidence}
                                if phase == "volatile_prefix"
                                else {"evidence": evidence, **changes}
                            )
                            row = await request(
                                client,
                                args.base,
                                ready["model"],
                                json.dumps(payload, ensure_ascii=False),
                                args.output_tokens,
                            )
                            row.update(
                                size=size,
                                round=round_index + 1,
                                phase=phase,
                                nonce=nonce,
                                revision=revision,
                            )
                            report["rows"].append(row)
                            print(json.dumps(row, ensure_ascii=False), flush=True)
                    continue
                nonce = uuid.uuid4().hex
                prefix = "合成测试批次 " + nonce + "\n" + data
                for phase in ("miss", "repeat", "changed_tail"):
                    suffix = "\n问题：请分析全部条目的条件。"
                    if phase == "changed_tail":
                        suffix = "\n问题：请分析全部条目的状态。"
                    row = await request(
                        client, args.base, ready["model"], prefix + suffix, args.output_tokens
                    )
                    row.update(size=size, round=round_index + 1, phase=phase, nonce=nonce)
                    report["rows"].append(row)
                    print(json.dumps(row, ensure_ascii=False), flush=True)
        final = (await client.get(args.base + "/health/ready")).json()
        if final["instance_id"] != ready["instance_id"]:
            raise RuntimeError("model instance changed during benchmark")
        report["memory_after"] = llm_memory()
        report["summary"] = summarize(report["rows"])
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps(
                {
                    "summary": report["summary"],
                    "memory_before": report["memory_before"],
                    "memory_after": report["memory_after"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:8100")
    parser.add_argument("--sizes", type=int, nargs="+", default=[1024, 8192, 32768])
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output-tokens", type=int, default=64)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--layout", action="store_true", help="比较变动字段放在证据前后时的实际缓存命中"
    )
    parser.add_argument(
        "--analyze-schema", action="store_true", help="只分析当前任务 Schema 的稳定前缀长度"
    )
    asyncio.run(benchmark(parser.parse_args()))
