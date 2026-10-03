import asyncio
import json

import pytest

from meetnote.config import Settings
from meetnote.domain.transcripts import ChunkInput
from meetnote.engine.minutes import text_blocks
from meetnote.llm.client import output_observer
from meetnote.llm.mock import MockClient
from meetnote.sessions.manager import SessionManager


class StreamingModel(MockClient):
    def __init__(self):
        self.hold = asyncio.Event()
        self.entered = asyncio.Event()
        self.minutes_calls = 0

    async def generate(self, task, instructions, context, schema):
        result = await super().generate(task, instructions, context, schema)
        if task == "minutes":
            self.minutes_calls += 1
            observer = output_observer.get()
            if observer:
                observer(None)
                raw = result.model_dump_json()
                for index in range(0, len(raw), 4):
                    observer(raw[index : index + 4])
                    await asyncio.sleep(0)
            self.entered.set()
            await self.hold.wait()
        return result


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"overview":{"text":"中文', "中文"),
        ('{"overview":{"text":"中文\\u4e', "中文"),
        ('{"overview":{"text":"中文\\n下一句', "中文\n下一句"),
        ('{"overview":{"text":"中文\\"引号', '中文"引号'),
    ],
)
def test_partial_json_exposes_only_decoded_text(raw, expected):
    assert text_blocks(raw) == {"overview.0": expected}
    assert not text_blocks('{"overview":{"fact_ids":["f000001"]}')
    assert not text_blocks(json.dumps({"overview": {"text": "原文索引 s1.0"}}))


async def wait_until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached")


async def test_draft_streams_before_response_and_finalization_reuses_it():
    model = StreamingModel()
    service = SessionManager(
        Settings(_env_file=None, provider="mock", checkpoint_seconds=0, minutes_interval=0.01),
        model,
    )
    session = service.create()
    try:
        await service.append(session.id, ChunkInput(text="陈宁周四完成验收。"))
        await asyncio.wait_for(model.entered.wait(), 2)
        assert session.snapshot()["minutes_preview"]["blocks"]["overview.0"]
        assert session.snapshot()["minutes"] is None
        assert session.status == "running"
        model.hold.set()
        await wait_until(lambda: session.snapshot()["minutes"] is not None)
        calls = model.minutes_calls
        await service.finalize(session.id)
        await session.task
        assert session.status == "completed"
        assert model.minutes_calls == calls
        assert session.snapshot()["remaining_chars"] == 0
        assert session.snapshot()["minutes_preview"] is None
    finally:
        await service.close()


async def test_new_tail_is_processed_while_draft_is_blocked_and_cancel_reclaims_it():
    model = StreamingModel()
    service = SessionManager(Settings(_env_file=None, provider="mock", checkpoint_seconds=0), model)
    session = service.create()
    try:
        await service.append(session.id, ChunkInput(text="陈宁周四完成验收。"))
        await asyncio.wait_for(model.entered.wait(), 2)
        await service.append(session.id, ChunkInput(text="王宁周五提交报告。"))
        await wait_until(lambda: session.engine.stream.consumed == session.chars)
        assert model.minutes_calls == 1
        await service.cancel(session)
        assert session.status == "cancelled"
        assert session.engine.minutes.task is None
        assert session.engine.knowledge.draft is None
    finally:
        await service.close()


async def test_final_tail_updates_coverage_before_completed():
    model = StreamingModel()
    model.hold.set()
    service = SessionManager(
        Settings(_env_file=None, provider="mock", checkpoint_seconds=0, minutes_interval=100), model
    )
    session = service.create()
    try:
        await service.append(session.id, ChunkInput(text="陈宁周四完成验收。"))
        await wait_until(lambda: session.engine.knowledge.draft is not None)
        await service.append(session.id, ChunkInput(text="王宁周五提交报告。"))
        await service.finalize(session.id)
        await session.task
        assert session.status == "completed"
        assert session.engine.knowledge.draft.coverage["consumed_chars"] == session.chars
        assert model.minutes_calls == 2
    finally:
        await service.close()
