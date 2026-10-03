"""在一次进程内重放完全相同的抽取请求，隔离决策差异。"""

import hashlib
import json

from meetnote.engine.models import create_model
from meetnote.evaluation.short import CASES, run_short
from meetnote.llm.client import strict_schema
from meetnote.llm.laya import LayaClient


async def comparison_info(settings):
    if settings.provider != "responses":
        raise ValueError("真实对照不能使用 mock 主模型")
    client = LayaClient(settings.laya_url, settings.laya_timeout)
    try:
        info = await client.info()
        if info.get("precision") != "bf16" or info.get("backend") != "vllm":
            raise ValueError("当前对照仅使用 BF16 vLLM 决策服务")
        return info
    finally:
        await client.close()


class ExtractionReplay:
    def __init__(self, base, records):
        self.base, self.records = base, records
        self.hits = self.misses = 0

    async def cache_identity(self, task):
        return (
            await self.base.cache_identity(task)
            if hasattr(self.base, "cache_identity")
            else ["responses"]
        )

    async def generate(self, task, instructions, context, schema):
        if task != "extract":
            return await self.base.generate(task, instructions, context, schema)
        key = hashlib.sha256(
            json.dumps(
                [instructions, context, strict_schema(schema)], ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        if key in self.records:
            self.hits += 1
            return schema.model_validate_json(self.records[key])
        self.misses += 1
        result = await self.base.generate(task, instructions, context, schema)
        self.records[key] = result.model_dump_json()
        return result

    async def close(self):
        await self.base.close()


async def run_paired(settings, emit=print, names=None):
    info = await comparison_info(settings)
    emit(json.dumps({"record": "comparison_model", "model": info}, ensure_ascii=False))
    records = {}
    outcomes = {}
    for backend in ("responses", "laya"):
        configuration = settings.model_copy(
            update={"decision_backend": backend, "laya_fallback": False}
        )
        model = ExtractionReplay(create_model(configuration), records)

        outcomes[backend] = {}

        def report(line):
            row = json.loads(line)
            outcomes[backend][row["case"]] = row["passed"]
            # 明细仍可由 check-short 查看；对照默认输出紧凑评测信息。
            emit(
                json.dumps(
                    {
                        "record": "paired_short",
                        "backend": backend,
                        "case": row["case"],
                        "passed": row["passed"],
                        "errors": row["errors"],
                        "stats": row["stats"],
                    },
                    ensure_ascii=False,
                )
            )

        failures = await run_short(configuration, names or list(CASES), report, model=model)
        emit(
            json.dumps(
                {
                    "record": "paired_summary",
                    "backend": backend,
                    "failures": failures,
                    "total": len(names or CASES),
                    "extract_replay_hits": model.hits,
                    "extract_new_requests": model.misses,
                },
                ensure_ascii=False,
            )
        )

    before, after = outcomes["responses"], outcomes["laya"]
    delta = {
        "record": "paired_delta",
        "total": len(before),
        "both_pass": sum(before[k] and after[k] for k in before),
        "regressions": [k for k in before if before[k] and not after[k]],
        "improvements": [k for k in before if not before[k] and after[k]],
        "both_fail": [k for k in before if not before[k] and not after[k]],
    }
    emit(json.dumps(delta, ensure_ascii=False))
    return delta
