import asyncio
from dataclasses import replace

import pytest

from meetnote.config import Settings
from meetnote.domain.results import (
    BoundaryDecision,
    CommittedSummary,
    MeetingLens,
    Signal,
    SummaryAtom,
)
from meetnote.domain.state import CycleState, initial_state
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.boundaries import consumed_prefix
from meetnote.engine.context import Context
from meetnote.engine.quality import filter_duplicate_atoms
from meetnote.engine.runtime import NODES, MeetingEngine, NoProgress
from meetnote.engine.scheduling import commit_debt_meter, work_candidate_builder
from meetnote.llm.client import ModelFailure
from meetnote.llm.mock import MockClient


def engine(model=None, **kwargs):
    events = []
    settings = Settings(
        _env_file=None,
        provider="mock",
        min_chars=10,
        window_chars=60,
        checkpoint_seconds=0,
        **kwargs,
    )
    return MeetingEngine(
        Context(settings, model or MockClient(), lambda kind, data: events.append((kind, data)))
    ), events


def chunk(text, seq=1, start=0):
    return TranscriptChunk(
        text=text, chunk_id=f"c{seq}", seq=seq, start=start, end=start + len(text)
    )


async def test_thirteen_nodes_and_multiwindow_flush():
    runtime, events = engine()
    text = "".join(f"议题{i}决定由第{i}组在周五完成独立的第{i}项验收工作。" for i in range(20))
    await runtime.append(chunk(text))
    await runtime.finalize()
    assert runtime.state["stream"].buffer == ""
    assert runtime.state["stream"].consumed == len(text)
    assert runtime.state["knowledge"].draft
    names = [data["node"] for kind, data in events if kind == "node.started"]
    assert names[:13] == [node.__name__ for node in NODES]
    summaries = runtime.state["knowledge"].summaries
    assert len(summaries) > 1
    assert all(summary.chunk_ids == ["c1"] for summary in summaries)
    assert max(s.end for s in summaries) == len(text)
    assert [kind for kind, _ in events].index("summary.committed") > [
        kind for kind, _ in events
    ].index("model.completed")


async def test_evidence_excludes_uncovered_chunks():
    runtime, _ = engine()
    await runtime.append(chunk("甲方确认方案一，李文负责落实。"))
    await runtime.finalize()
    first = runtime.state["knowledge"].summaries[0]
    assert first.chunk_ids == ["c1"]
    assert first.start == 0


class BrokenSummary(MockClient):
    async def generate(self, task, instructions, context, schema):
        if task == "summary":
            raise ModelFailure("unavailable")
        return await super().generate(task, instructions, context, schema)


async def test_model_failure_preserves_input():
    runtime, _ = engine(BrokenSummary())
    await runtime.append(chunk("这是需要原样保留的一段会议内容。"))
    with pytest.raises(ModelFailure):
        await runtime.finalize()
    assert runtime.state["stream"].buffer == "这是需要原样保留的一段会议内容。"
    assert not runtime.state["knowledge"].summaries


class BadBoundary(MockClient):
    async def generate(self, task, instructions, context, schema):
        if task == "summary":
            return BoundaryDecision(
                should_emit=True,
                summary_atoms=[SummaryAtom(summary="不应发布")],
                remaining_text="原文里没有这个文本",
                summary_end_anchor="不存在",
            )
        return await super().generate(task, instructions, context, schema)


async def test_finalization_stops_on_no_progress_without_publishing():
    runtime, events = engine(BadBoundary())
    await runtime.append(chunk("确认明天由小陈处理验收。"))
    with pytest.raises(NoProgress):
        await runtime.finalize()
    assert runtime.state["stream"].buffer
    assert not any(kind == "summary.committed" for kind, _ in events)


def test_boundary_ambiguity_and_whitespace_mapping():
    decision = BoundaryDecision(
        should_emit=True, summary_atoms=[SummaryAtom(summary="内容")], summary_end_anchor="确认"
    )
    assert consumed_prefix("确认，稍后再确认", decision, False) is None
    decision.remaining_text = "乙方继续说明"
    assert consumed_prefix("甲方已确认。乙方 继续说明", decision, False) == 6


def test_fact_changes_are_not_deduplicated_by_shared_keywords():
    old = SummaryAtom(summary="预算为10万元", keywords=["预算", "采购"], key_points=["10万元"])
    new = SummaryAtom(summary="预算改为12万元", keywords=["预算", "采购"], key_points=["12万元"])
    accepted, duplicates = filter_duplicate_atoms([new, old], [old])
    assert accepted == [new]
    assert duplicates == 1


async def test_debt_and_idle_lens_thresholds():
    runtime, _ = engine()
    state = initial_state()
    state["cycle"] = CycleState(text="文本", signal=Signal(commit_points=2))
    state.update(await commit_debt_meter(state, runtime.context))
    assert state["schedule"].mode == "watch"
    state.update(await commit_debt_meter(state, runtime.context))
    assert state["schedule"].debt == 4
    assert state["schedule"].mode == "stage2"
    state.update(await work_candidate_builder(state, runtime.context))
    assert state["cycle"].candidates == ("summary",)
    summaries = tuple(
        CommittedSummary(summary=f"摘要{i}", id=str(i), chunk_ids=["c"], start=0, end=1)
        for i in range(2)
    )
    state["schedule"] = replace(state["schedule"], debt=0, mode="watch")
    state["knowledge"] = replace(state["knowledge"], summaries=summaries)
    state.update(await work_candidate_builder(state, runtime.context))
    assert state["cycle"].candidates == ("lens",)
    state["knowledge"] = replace(state["knowledge"], lens=MeetingLens(confidence=0.8))
    state["schedule"] = replace(state["schedule"], lens_source_count=2)
    state.update(await work_candidate_builder(state, runtime.context))
    assert state["cycle"].candidates == ("hint",)


async def test_cancellation_propagates_into_model():
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    class Slow(MockClient):
        async def generate(self, *args):
            entered.set()
            try:
                await asyncio.sleep(60)
            finally:
                cancelled.set()

    runtime, _ = engine(Slow())
    task = asyncio.create_task(runtime.append(chunk("待处理文本。" * 20)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled.is_set()


async def test_final_plan_and_composer_receive_all_evidence():
    seen = {}

    class Capture(MockClient):
        async def generate(self, task, instructions, context, schema):
            seen[task] = context
            return await super().generate(task, instructions, context, schema)

    runtime, _ = engine(Capture())
    summaries = tuple(
        CommittedSummary(
            summary=f"第{i}项已确认事实", id=f"s{i}", chunk_ids=["c"], start=i, end=i + 1
        )
        for i in range(20)
    )
    runtime.state["knowledge"] = replace(runtime.state["knowledge"], summaries=summaries)
    await runtime.finalize()
    assert len(seen["plan"]["summaries"]) == 20
    assert len(seen["compose"]["summaries"]) == 20
    assert runtime.state["knowledge"].plan_revision == 1
    assert len(runtime.state["knowledge"].draft.evidence_trace) == 20


async def test_planning_fallback_is_visible_and_keeps_summary():
    class FailedPlanner(MockClient):
        async def generate(self, task, instructions, context, schema):
            if task in {"plan", "compose"}:
                raise ModelFailure("unavailable")
            return await super().generate(task, instructions, context, schema)

    runtime, events = engine(FailedPlanner())
    await runtime.append(chunk("周四验收，陈宁负责。"))
    await runtime.finalize()
    draft = runtime.state["knowledge"].draft
    assert "周四验收" in draft.markdown
    assert len(draft.delivery_warnings) == 2
    assert len([1 for kind, _ in events if kind == "node.degraded"]) == 2
