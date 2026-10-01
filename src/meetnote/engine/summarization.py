from dataclasses import replace

from meetnote.domain.results import BoundaryDecision, CommittedSummary
from meetnote.domain.state import EngineState, StatePatch
from meetnote.engine.boundaries import consumed_prefix
from meetnote.engine.context import Context
from meetnote.engine.quality import (
    filter_atoms_against_carry,
    filter_duplicate_atoms,
    keywords_from_atoms,
    merge_unique,
)


async def summary_worker(state: EngineState, ctx: Context) -> StatePatch:
    cycle, knowledge = state["cycle"], state["knowledge"]
    if "summary" not in cycle.jobs:
        return {}
    stream = state["stream"]
    end = stream.consumed + len(cycle.text)
    sources = [
        {
            "chunk_id": chunk.chunk_id,
            "speaker": chunk.speaker,
            "timestamp": chunk.timestamp,
            "text": chunk.text[max(0, stream.consumed - chunk.start) : end - chunk.start],
        }
        for chunk in stream.chunks
        if chunk.end > stream.consumed and chunk.start < end
    ]
    decision = await ctx.invoke(
        "summary",
        {
            "buffer_text": cycle.text,
            "source_chunks": sources,
            "force_flush": state["mode"] == "flush",
            "checkpoint_reason": cycle.reason,
            "carry_context": {
                "summaries": [s.summary for s in knowledge.summaries[-3:]],
                "signal": cycle.signal.model_dump() if cycle.signal else None,
                "lens": knowledge.lens.model_dump() if knowledge.lens else None,
            },
        },
        BoundaryDecision,
    )
    return {"cycle": replace(cycle, decision=decision)}


async def bucket_enricher(state: EngineState, ctx: Context) -> StatePatch:
    cycle, stream, knowledge, schedule = (
        state["cycle"],
        state["stream"],
        state["knowledge"],
        state["schedule"],
    )
    if not cycle.text:
        return {}
    signals = knowledge.recent_signals
    loops = list(knowledge.open_loops)
    if cycle.signal:
        signals = (*signals, cycle.signal)[-6:]
        loops += cycle.signal.open_slots + cycle.signal.asr_risks
    decision = cycle.decision
    if decision and decision.carry_context_note:
        loops.append(decision.carry_context_note)
    knowledge = replace(
        knowledge, recent_signals=signals, open_loops=tuple(merge_unique([], loops)[-20:])
    )
    if decision is None or not decision.should_emit:
        return {"knowledge": knowledge}
    end = consumed_prefix(cycle.text, decision, state["mode"] == "flush")
    if end is None:
        ctx.emit("summary.rejected", {"reason": "boundary_not_unique", "cycle": cycle.number})
        return {"knowledge": knowledge}
    accepted, deferred = filter_atoms_against_carry(
        decision.summary_atoms, cycle.text[:end], cycle.text[end:]
    )
    if deferred:
        # 无法证明候选只覆盖已消费前缀时，整轮保留，避免丢掉被过滤的内容。
        ctx.emit("summary.rejected", {"reason": "carry_overlap", "cycle": cycle.number})
        return {"knowledge": knowledge}
    accepted, duplicates = filter_duplicate_atoms(accepted, list(knowledge.summaries))
    if not accepted and not duplicates:
        return {"knowledge": knowledge}
    absolute_end = stream.consumed + end
    ids = [
        chunk.chunk_id
        for chunk in stream.chunks
        if chunk.end > stream.consumed and chunk.start < absolute_end
    ]
    new = tuple(
        CommittedSummary(
            **atom.model_dump(),
            id=f"summary_{len(knowledge.summaries) + i + 1:04d}",
            chunk_ids=ids,
            start=stream.consumed,
            end=absolute_end,
        )
        for i, atom in enumerate(accepted)
    )
    keywords, rejected = keywords_from_atoms(list(new))
    summaries = (*knowledge.summaries, *new)
    knowledge = replace(
        knowledge,
        summaries=summaries,
        keywords=tuple(merge_unique(list(knowledge.keywords), keywords)[-32:]),
    )
    for atom in new:
        ctx.emit("summary.committed", atom.model_dump(mode="json"))
    ctx.emit(
        "evidence.updated",
        {"consumed_chars": end, "duplicates": duplicates, "rejected_keywords": rejected},
    )
    return {
        "stream": replace(stream, buffer=stream.buffer[end:], consumed=absolute_end),
        "knowledge": knowledge,
        "schedule": replace(schedule, debt=0, additions=0, mode="watch"),
    }
