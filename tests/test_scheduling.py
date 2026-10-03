import asyncio

import pytest

from meetnote.config import Settings
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ModelFailure
from meetnote.llm.mock import MockClient


def make_engine(model=None, **options):
    return MeetingEngine(
        Context(
            Settings(_env_file=None, provider="mock", checkpoint_seconds=0, **options),
            model or MockClient(),
            lambda *_: None,
        )
    )


async def chunks(count=4):
    offset = 0
    for i in range(count):
        text = f"确认第{i}项方案。"
        yield TranscriptChunk(
            chunk_id=str(i), seq=i + 1, text=text, start=offset, end=offset + len(text)
        )
        offset += len(text)


class Reordered(MockClient):
    def __init__(self):
        self.started = []
        self.first = asyncio.Event()
        self.second = asyncio.Event()
        self.active = 0
        self.peak = 0
        self.cancelled = 0

    async def generate(self, task, instructions, context, schema):
        if task != "extract":
            return await super().generate(task, instructions, context, schema)
        source = context["sources"][0]["id"]
        self.started.append(source)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if source == "s1.0":
                await self.first.wait()
            if source == "s2.0":
                self.second.set()
            return await super().generate(task, instructions, context, schema)
        except asyncio.CancelledError:
            self.cancelled += 1
            raise
        finally:
            self.active -= 1


async def test_parallel_extraction_orders_commits_and_bounds_prefetch():
    model = Reordered()
    engine = make_engine(model, extraction_window=2)
    run = asyncio.create_task(engine.run(chunks()))
    await asyncio.wait_for(model.second.wait(), 2)
    await asyncio.sleep(0)
    assert engine.stream.consumed == 0
    assert len(model.started) == 2
    assert engine.stream.processed <= 3  # 至多一个输入包预取
    model.first.set()
    await asyncio.wait_for(run, 2)
    assert model.peak == 2
    assert engine.stream.consumed == engine.stream.cursor
    assert [f.id for f in engine.knowledge.facts] == [f"f{i:06d}" for i in range(1, 5)]
    serial = make_engine()
    async for chunk in chunks():
        await serial.append(chunk)
    await serial.finalize()
    assert engine.knowledge.facts == serial.knowledge.facts
    assert engine.knowledge.revisions == serial.knowledge.revisions
    assert engine.knowledge.draft == serial.knowledge.draft


async def test_cancel_reclaims_tasks_without_consuming_pending_sources():
    model = Reordered()
    engine = make_engine(model, extraction_window=2)
    run = asyncio.create_task(engine.run(chunks()))
    await asyncio.wait_for(model.second.wait(), 2)
    run.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run
    assert engine.stream.consumed == 0
    assert model.active == 0
    assert model.cancelled == 1
    assert not engine.finalized


async def test_checkpoint_fires_while_input_iterator_waits():
    engine = MeetingEngine(
        Context(
            Settings(_env_file=None, provider="mock", checkpoint_seconds=0.02),
            MockClient(),
            lambda *_: None,
        )
    )
    release = asyncio.Event()

    async def slow():
        async for chunk in chunks(1):
            yield chunk
        await release.wait()

    run = asyncio.create_task(engine.run(slow()))
    for _ in range(100):
        if engine.stream.consumed:
            break
        await asyncio.sleep(0.005)
    assert engine.stream.consumed == engine.stream.cursor > 0
    release.set()
    await asyncio.wait_for(run, 2)
    assert engine.finalized


async def test_fatal_head_error_does_not_commit_later_results():
    class Failing(Reordered):
        async def generate(self, task, instructions, context, schema):
            if task == "extract" and context["sources"][0]["id"] == "s1.0":
                await self.second.wait()
                raise ModelFailure("fatal")
            return await super().generate(task, instructions, context, schema)

    engine = make_engine(Failing(), extraction_window=2)
    with pytest.raises(ModelFailure, match="fatal"):
        await asyncio.wait_for(engine.run(chunks()), 2)
    assert engine.stream.consumed == 0
    assert not engine.knowledge.facts


async def test_local_repair_only_requests_bad_record_and_keeps_good_record():
    class Repair(MockClient):
        def __init__(self):
            self.requests = []

        async def generate(self, task, instructions, context, schema):
            result = await super().generate(task, instructions, context, schema)
            if task == "extract":
                self.requests.append([s["id"] for s in context["sources"]])
                if len(self.requests) == 1:
                    data = result.model_dump()
                    data["s2.0"]["facts"][0]["source_ids"] = ["s1.0"]
                    return schema.model_validate(data)
            return result

    model = Repair()
    engine = make_engine(model, local_extraction_retry=True)
    async for chunk in chunks(2):
        engine.ingest(chunk)
    await engine.finalize()
    assert model.requests == [["s1.0", "s2.0"], ["s2.0"]]
    assert engine.stream.consumed == engine.stream.cursor
    assert len(engine.knowledge.facts) == 2
    assert engine.context.stats["local_extraction_retries"] == 1


async def test_split_invalidates_later_prefetch_and_rebuilds_context():
    class Split(MockClient):
        def __init__(self):
            self.requests = []
            self.later = asyncio.Event()

        async def generate(self, task, instructions, context, schema):
            if task == "extract":
                ids = [s["id"] for s in context["sources"]]
                self.requests.append(ids)
                if ids == ["s2.0"]:
                    self.later.set()
                if ids == ["s1.0"]:
                    await self.later.wait()
                    raise ModelFailure("response_incomplete")
            return await super().generate(task, instructions, context, schema)

    model = Split()
    engine = make_engine(model, extraction_window=2, batch_sources=1)
    first = "确认第一个方案。确认第二个方案。"
    engine.ingest(TranscriptChunk(chunk_id="first", seq=1, text=first, start=0, end=len(first)))
    engine.ingest(
        TranscriptChunk(
            chunk_id="second", seq=2, text="确认第三个方案。", start=len(first), end=len(first) + 8
        )
    )
    await asyncio.wait_for(engine.run(chunks(0)), 2)
    assert model.requests.count(["s2.0"]) == 2
    assert engine.stream.consumed == engine.stream.cursor
    assert len(engine.knowledge.facts) == 3
    assert engine.context.stats["extraction_splits"] == 1


async def test_parallel_selection_preserves_order_and_cancels_on_failure():
    engine = make_engine(parallel_chapters=True, model_concurrency=2)
    active = 0
    peak = 0

    async def select(i):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep((3 - i) * 0.005)
            return i
        finally:
            active -= 1

    assert await engine.bounded_map(select, [0, 1, 2]) == [0, 1, 2]
    assert peak == 2
    assert active == 0

    async def fail(i):
        if i == 1:
            raise ModelFailure("selection_failed")
        return await select(i)

    with pytest.raises(ModelFailure, match="selection_failed"):
        await engine.bounded_map(fail, [0, 1])
    assert active == 0


async def test_invalid_local_repair_is_not_cached_or_retried_indefinitely():
    class AlwaysInvalid(MockClient):
        def __init__(self):
            self.calls = 0

        async def generate(self, task, instructions, context, schema):
            result = await super().generate(task, instructions, context, schema)
            if task == "extract":
                self.calls += 1
                data = result.model_dump()
                data["s1.0"]["facts"][0]["source_ids"] = ["nonexistent"]
                return schema.model_validate(data)
            return result

    model = AlwaysInvalid()
    engine = make_engine(model, local_extraction_retry=True)
    async for chunk in chunks(1):
        engine.ingest(chunk)
    with pytest.raises(ModelFailure, match="task_validation_failed"):
        await engine.extract(list(engine.stream.pending))
    assert model.calls == 2
    assert not engine.context.cache
    assert engine.stream.consumed == 0


def test_minutes_schema_binds_references_without_changing_output_fields():
    from pydantic import ValidationError

    from meetnote.domain.results import ReaderMinutes, reader_minutes_schema
    from meetnote.llm.client import strict_schema

    schema = reader_minutes_schema(["f000001", "f000002"])
    assert set(schema.model_fields) == set(ReaderMinutes.model_fields)
    wire = strict_schema(schema)
    assert wire["$defs"]["GroundedParagraph"]["properties"]["fact_ids"]["items"]["enum"] == [
        "f000001",
        "f000002",
    ]
    payload = {
        "overview": {"text": "会议讨论发布安排。", "fact_ids": ["f000001"]},
        "conclusions": [],
        "discussion": [],
        "actions": [],
        "open_questions": [],
    }
    assert schema.model_validate(payload).overview.fact_ids == ["f000001"]
    payload["overview"]["fact_ids"] = ["s1.0"]
    with pytest.raises(ValidationError):
        schema.model_validate(payload)
