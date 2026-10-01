from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel

from meetnote.config import Settings
from meetnote.llm.client import ModelClient

T = TypeVar("T", bound=BaseModel)
Emitter = Callable[[str, dict], None]


@dataclass
class Context:
    settings: Settings
    model: ModelClient
    emit: Emitter

    async def invoke(self, task: str, context: dict, schema: type[T]) -> T:
        prompt = files("meetnote.prompts").joinpath(f"{task}.txt").read_text(encoding="utf-8")
        started = perf_counter()
        self.emit("model.started", {"task": task, "input": context})
        result = await self.model.generate(task, prompt, context, schema)
        self.emit(
            "model.completed",
            {
                "task": task,
                "elapsed_ms": round((perf_counter() - started) * 1000),
                "output": result.model_dump(mode="json"),
            },
        )
        return result
