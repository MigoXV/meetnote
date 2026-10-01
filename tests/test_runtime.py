"""流水线的状态提交、事件及取消契约。"""

import asyncio
from dataclasses import replace

import pytest

from meetnote.config import Settings
from meetnote.domain.state import EngineState, StatePatch
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine import runtime
from meetnote.engine.context import Context
from meetnote.llm.mock import MockClient


def make_engine():
    events = []
    engine = runtime.MeetingEngine(
        Context(
            Settings(_env_file=None, provider="mock"),
            MockClient(),
            lambda kind, data: events.append((kind, data)),
        )
    )
    return engine, events


async def test_partial_updates_clear_input_and_preserve_untouched_fields(monkeypatch):
    engine, events = make_engine()
    stream = engine.state["stream"]
    chunk = TranscriptChunk(chunk_id="c1", text="确认", seq=1, start=0, end=2)

    async def clear(state: EngineState, ctx: Context) -> StatePatch:
        assert state["input"] is chunk
        assert state["mode"] == "flush"
        return {"input": None, "title": "已确认"}

    async def skip(state: EngineState, ctx: Context) -> StatePatch:
        assert state["input"] is None
        assert state["title"] == "已确认"
        return {}

    monkeypatch.setattr(runtime, "NODES", (clear, skip))
    await engine.cycle("flush", chunk)
    assert engine.state["stream"] is stream
    assert engine.state["input"] is None
    assert engine.state["title"] == "已确认"
    assert [(kind, data["node"]) for kind, data in events] == [
        ("node.started", "clear"),
        ("node.completed", "clear"),
        ("node.started", "skip"),
        ("node.skipped", "skip"),
    ]


@pytest.mark.parametrize("failure_at", [0, 1, 2])
@pytest.mark.parametrize("cancel", [False, True])
async def test_failure_or_cancellation_keeps_last_successful_state(monkeypatch, failure_at, cancel):
    engine, events = make_engine()
    initial = engine.state
    completed = []
    entered = asyncio.Event()
    failure = ValueError("failed")

    def step(index):
        async def execute(state: EngineState, ctx: Context) -> StatePatch:
            if index == failure_at:
                entered.set()
                if cancel:
                    await asyncio.Event().wait()
                raise failure
            completed.append(index)
            return {"stream": replace(state["stream"], processed=index + 1)}

        execute.__name__ = f"step_{index}"
        return execute

    monkeypatch.setattr(runtime, "NODES", tuple(step(i) for i in range(3)))
    task = asyncio.create_task(engine.cycle("flush"))
    await entered.wait()
    if cancel:
        task.cancel()
    with pytest.raises(asyncio.CancelledError if cancel else ValueError) as caught:
        await task
    if not cancel:
        assert caught.value is failure
    assert completed == list(range(failure_at))
    assert engine.state["stream"].processed == failure_at
    if failure_at == 0:
        assert engine.state is initial
        assert engine.state["mode"] == "normal"
    else:
        assert engine.state["mode"] == "flush"
    expected = []
    for index in range(failure_at):
        expected.extend([("node.started", f"step_{index}"), ("node.completed", f"step_{index}")])
    expected.append(("node.started", f"step_{failure_at}"))
    if not cancel:
        expected.append(("node.failed", f"step_{failure_at}"))
        assert events[-1][1]["error"] == "ValueError"
    assert [(kind, data["node"]) for kind, data in events] == expected
