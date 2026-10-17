"""任务边界：预算、校验、内存复用和统计。"""

import hashlib
import json
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
from time import perf_counter

from meetnote.config import Settings
from meetnote.llm.budget import Budget
from meetnote.llm.client import (
    ModelClient,
    ModelFailure,
    output_observer,
    request_observer,
    strict_schema,
)


@dataclass
class Context:
    settings: Settings
    model: ModelClient
    emit: Callable
    cache: OrderedDict = field(default_factory=OrderedDict)
    cache_size: int = 0
    stats: dict = field(
        default_factory=lambda: {
            "tasks": 0,
            "requests": 0,
            "cache_hits": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "usage_unknown": 0,
        }
    )
    vectors: object = None

    def __post_init__(self):
        self.budget = Budget(self.settings)
        if self.vectors is None:
            from meetnote.embeddings.client import Vectors
            from meetnote.embeddings.mock import MockEncoder

            encoder = MockEncoder() if self.settings.provider == "mock" else None
            self.vectors = Vectors(self.settings, self.stats, self.emit, encoder)

    def prompt(self, task):
        return files("meetnote.prompts").joinpath(f"{task}.txt").read_text(encoding="utf-8")

    def fits(self, task, payload, schema):
        return (
            self.budget.input_count(self.prompt(task), payload, schema)
            <= self.settings.input_token_budget
        )

    def observe(self, data):
        backend = data.get("backend", "responses")
        self.stats[backend + "_requests"] = self.stats.get(backend + "_requests", 0) + 1
        self.stats[backend + "_elapsed_ms"] = self.stats.get(backend + "_elapsed_ms", 0) + data.get(
            "elapsed_ms", 0
        )
        self.stats["requests"] += 1
        key = data["task"] + "_requests"
        self.stats[key] = self.stats.get(key, 0) + 1
        usage = data.get("usage")
        if usage is None:
            self.stats["usage_unknown"] += 1
        else:
            for key in ("input_tokens", "output_tokens"):
                self.stats[key] += usage.get(key) or 0
            self.stats["cached_tokens"] += (usage.get("input_tokens_details") or {}).get(
                "cached_tokens"
            ) or 0
        tasks = self.stats.setdefault("by_task", {})
        task = tasks.setdefault(
            data["task"],
            {
                "requests": 0,
                "elapsed_ms": 0,
                "queue_ms": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "errors": 0,
                "first_output_ms": [],
            },
        )
        task["requests"] += 1
        task["elapsed_ms"] += data.get("elapsed_ms", 0)
        task["queue_ms"] += data.get("queue_ms", 0)
        task["errors"] += bool(data.get("error"))
        for key in ("input_tokens", "output_tokens"):
            task[key] += (usage or {}).get(key) or 0
        if data.get("first_output_ms") is not None:
            # 有界采样；累计计数不截断。
            task["first_output_ms"] = (task["first_output_ms"] + [data["first_output_ms"]])[-256:]
        self.emit("model.request", data)

    async def invoke(
        self,
        task,
        context,
        schema,
        validate=None,
        repair=None,
        retry_validation=True,
        on_output=None,
    ):
        prompt = self.prompt(task)
        count = self.budget.check(prompt, context, schema)
        self.stats["tasks"] += 1
        key_data = [
            task,
            self.settings.base_url,
            self.settings.model,
            self.settings.temperature,
            self.settings.thinking,
            self.settings.max_output_tokens,
            self.budget.fingerprint,
            prompt,
            strict_schema(schema),
            context,
        ]
        key = hashlib.sha256(
            json.dumps(key_data, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        if key in self.cache:
            raw, size = self.cache[key]
            result = schema.model_validate_json(raw)
            if validate:
                validate(result)
            self.cache.move_to_end(key)
            self.stats["cache_hits"] += 1
            self.emit("model.reused", {"task": task})
            return result
        started = perf_counter()
        self.emit(
            "model.started",
            {
                "task": task,
                "input": context,
                "estimated_input_tokens": count,
                "tokenizer": self.budget.fingerprint,
            },
        )
        token = request_observer.set(self.observe)
        output_token = output_observer.set(on_output)
        try:
            result = await self.model.generate(task, prompt, context, schema)
            if validate:
                try:
                    validate(result)
                except ModelFailure as exc:
                    self.emit(
                        "model.validation_failed",
                        {"task": task, "error": str(exc), "output": result.model_dump()},
                    )
                    if not retry_validation:
                        raise ModelFailure("task_validation_failed:" + str(exc)) from exc
                    correction = {
                        **context,
                        "validation_error": str(exc),
                        "instruction": "重新输出完整结果，修正引用和覆盖错误。",
                    }
                    with_output = {**correction, "invalid_output": result.model_dump(mode="json")}
                    if self.fits(task, with_output, schema):
                        correction = with_output
                    self.stats["validation_retries"] = self.stats.get("validation_retries", 0) + 1
                    if repair is not None and getattr(exc, "source_ids", ()):
                        result = await repair(result, exc)
                    else:
                        self.budget.check(prompt, correction, schema)
                        result = await self.model.generate(task, prompt, correction, schema)
                    try:
                        validate(result)
                    except ModelFailure as final_error:
                        raise ModelFailure(
                            "task_validation_failed:" + str(final_error)
                        ) from final_error
        except BaseException as exc:
            self.emit(
                "model.failed",
                {
                    "task": task,
                    "error": str(exc),
                    "elapsed_ms": round((perf_counter() - started) * 1000),
                },
            )
            raise
        finally:
            request_observer.reset(token)
            output_observer.reset(output_token)
        raw = result.model_dump_json()
        size = len(raw.encode())
        if self.settings.cache_entries and size <= self.settings.cache_bytes:
            self.cache[key] = (raw, size)
            self.cache_size += size
            while (
                len(self.cache) > self.settings.cache_entries
                or self.cache_size > self.settings.cache_bytes
            ):
                _, (_, removed) = self.cache.popitem(last=False)
                self.cache_size -= removed
        self.emit(
            "model.completed",
            {
                "task": task,
                "output": result.model_dump(mode="json"),
                "elapsed_ms": round((perf_counter() - started) * 1000),
            },
        )
        return result
