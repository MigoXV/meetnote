"""异步 Responses 适配器。不持有业务历史，不改写任务指令。"""

from __future__ import annotations

import asyncio
import json
from contextvars import ContextVar
from copy import deepcopy
from time import perf_counter
from typing import Any, Protocol, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from meetnote.config import Settings

request_observer = ContextVar("request_observer", default=None)
output_observer = ContextVar("output_observer", default=None)

T = TypeVar("T", bound=BaseModel)


class ModelFailure(RuntimeError):
    pass


class ModelClient(Protocol):
    async def generate(self, task: str, instructions: str, context: dict, schema: type[T]) -> T: ...

    async def close(self) -> None: ...


def strict_schema(schema: type[BaseModel]) -> dict[str, Any]:
    """Responses strict 要求所有属性 required；可空字段由自身类型表达。"""
    document = deepcopy(schema.model_json_schema())

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            value.pop("default", None)
            if value.get("type") == "object":
                value["additionalProperties"] = False
                value["required"] = list(value.get("properties", {}))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    return document


class ResponsesClient:
    def __init__(self, settings: Settings, transport: openai.AsyncOpenAI | None = None):
        self.settings = settings
        self.client = transport or openai.AsyncOpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key,
            timeout=settings.model_timeout,
            max_retries=0,
        )
        from meetnote.llm.budget import Budget

        self.budget = Budget(settings)
        self.capacity = asyncio.Semaphore(settings.model_concurrency)

    async def generate(self, task: str, instructions: str, context: dict, schema: type[T]) -> T:
        payload = json.dumps(context, ensure_ascii=False)
        network_retries = 0
        corrections = 0
        waiting = perf_counter()
        async with self.capacity:
            queue_ms = round((perf_counter() - waiting) * 1000)
            while True:
                try:
                    raw = await asyncio.wait_for(
                        self._stream(instructions, payload, schema, task, queue_ms),
                        self.settings.model_timeout,
                    )
                    return schema.model_validate_json(raw)
                except ValidationError as exc:
                    if corrections:
                        raise ModelFailure("structured_output_invalid") from exc
                    corrections += 1
                    payload = json.dumps(
                        {
                            "task_input": context,
                            "invalid_output": raw,
                            "correction": "请纠正输出，严格符合 JSON Schema，勿添加字段。",
                        },
                        ensure_ascii=False,
                    )
                except (
                    openai.APIConnectionError,
                    openai.RateLimitError,
                    openai.InternalServerError,
                    asyncio.TimeoutError,
                ) as exc:
                    if network_retries:
                        raise ModelFailure("model_unavailable") from exc
                    network_retries += 1
                    await asyncio.sleep(0.5)
                except openai.APIError as exc:
                    raise ModelFailure("model_request_failed") from exc

    async def _stream(
        self, instructions: str, payload: str, schema: type[T], task="probe", queue_ms=0
    ) -> str:
        estimated = self.budget.check(instructions, json.loads(payload), schema)
        started = perf_counter()
        metadata = {
            "started_at": started,
            "first_output_ms": None,
            "task": task,
            "usage": None,
            "response_id": None,
            "error": None,
            "queue_ms": queue_ms,
            "estimated_input_tokens": estimated,
        }
        observer = request_observer.get()
        try:
            return await self._read_stream(instructions, payload, schema, task, metadata)
        except BaseException as exc:
            metadata["error"] = str(exc) or type(exc).__name__
            raise
        finally:
            metadata["elapsed_ms"] = round((perf_counter() - started) * 1000)
            if observer:
                observer(metadata)

    async def _read_stream(self, instructions, payload, schema, task, metadata):
        observer = output_observer.get()
        if observer:
            observer(None)  # 每次网络/结构重试开始新的预览，不能拼接不同响应。
        stream = await self.client.responses.create(
            model=self.settings.model,
            instructions=instructions,
            input=payload,
            stream=True,
            store=False,
            temperature=self.settings.temperature,
            max_output_tokens=min(
                self.settings.max_output_tokens, 4096 if task == "extract" else 2048
            ),
            text={
                "format": {
                    "type": "json_schema",
                    "name": schema.__name__,
                    "strict": True,
                    "schema": strict_schema(schema),
                }
            },
            extra_body={"enable_thinking": self.settings.thinking},
        )
        chunks: list[str] = []
        output_chars = checked_chars = trailing_whitespace = 0
        completed = False
        try:
            async for event in stream:
                if getattr(event, "response", None):
                    response = event.response
                    metadata["response_id"] = response.id
                    usage = getattr(response, "usage", None)
                    metadata["usage"] = usage.model_dump() if usage else None
                    server_error = getattr(response, "error", None)
                    metadata["server_error"] = server_error.model_dump() if server_error else None
                    details = getattr(response, "incomplete_details", None)
                    metadata["incomplete_details"] = details.model_dump() if details else None
                if event.type == "response.output_text.delta":
                    if event.delta and metadata["first_output_ms"] is None:
                        metadata["first_output_ms"] = round(
                            (perf_counter() - metadata["started_at"]) * 1000
                        )
                    chunks.append(event.delta)
                    if observer:
                        observer(event.delta)
                    output_chars += len(event.delta)
                    trailing_whitespace = (
                        trailing_whitespace + len(event.delta)
                        if event.delta.isspace()
                        else len(event.delta) - len(event.delta.rstrip())
                    )
                    if trailing_whitespace > 256:
                        raise ModelFailure("response_incomplete:whitespace_loop")
                    if output_chars - checked_chars >= 512:
                        checked_chars = output_chars
                        partial = "".join(chunks)
                        tail = partial[-512:]
                        previous = partial.rfind(tail, 0, len(partial) - 512)
                        if previous >= 0 and partial.find(tail, 0, previous) >= 0:
                            raise ModelFailure("response_incomplete:repeated_output")
                elif event.type == "response.completed":
                    if event.response.status != "completed":
                        raise ModelFailure("response_incomplete")
                    completed = True
                elif event.type in {
                    "error",
                    "response.failed",
                    "response.incomplete",
                    "response.refusal.done",
                }:
                    raise ModelFailure(f"{event.type}:{metadata.get('incomplete_details')}")
        except BaseException:
            # 仅进入有界内存事件，便于定位重复生成或截断；不写入日志或文件。
            metadata["partial_output_tail"] = "".join(chunks)[-2000:]
            raise
        finally:
            await stream.close()
        if not completed:
            raise ModelFailure("stream_ended_without_completion")
        return "".join(chunks)

    async def close(self) -> None:
        await self.client.close()
