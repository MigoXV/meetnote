"""异步 Responses 适配器。不持有业务历史，不改写任务指令。"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any, Protocol, TypeVar

import openai
from pydantic import BaseModel, ValidationError

from meetnote.config import Settings

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
        self.capacity = asyncio.Semaphore(settings.model_concurrency)

    async def generate(self, task: str, instructions: str, context: dict, schema: type[T]) -> T:
        payload = json.dumps(context, ensure_ascii=False)
        network_retries = 0
        corrections = 0
        async with self.capacity:
            while True:
                try:
                    raw = await asyncio.wait_for(
                        self._stream(instructions, payload, schema), self.settings.model_timeout
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

    async def _stream(self, instructions: str, payload: str, schema: type[T]) -> str:
        stream = await self.client.responses.create(
            model=self.settings.model,
            instructions=instructions,
            input=payload,
            stream=True,
            store=False,
            temperature=self.settings.temperature,
            max_output_tokens=self.settings.max_output_tokens,
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
        completed = False
        try:
            async for event in stream:
                if event.type == "response.output_text.delta":
                    chunks.append(event.delta)
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
                    raise ModelFailure(event.type)
        finally:
            await stream.close()
        if not completed:
            raise ModelFailure("stream_ended_without_completion")
        return "".join(chunks)

    async def close(self) -> None:
        await self.client.close()
