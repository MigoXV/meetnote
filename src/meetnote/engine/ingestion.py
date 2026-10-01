from dataclasses import replace
from time import monotonic

from meetnote.domain.state import CycleState, EngineState, StatePatch
from meetnote.engine.context import Context


async def bucket_accumulator(state: EngineState, ctx: Context) -> StatePatch:
    chunk = state["input"]
    if chunk is None:
        return {}
    stream = state["stream"]
    return {
        "stream": replace(
            stream,
            chunks=(*stream.chunks, chunk),
            buffer=stream.buffer + chunk.text,
            cursor=chunk.end,
            processed=stream.processed + 1,
        ),
        "input": None,
    }


async def cycle_slice_builder(state: EngineState, ctx: Context) -> StatePatch:
    stream = state["stream"]
    settings = ctx.settings
    ready = len(stream.buffer) >= settings.window_chars or (
        len(stream.buffer) >= settings.min_chars
        and monotonic() - stream.last_check >= settings.checkpoint_seconds
    )
    if not stream.buffer or (not ready and state["mode"] == "normal"):
        return {"cycle": CycleState(number=state["cycle"].number)}
    text = stream.buffer[: settings.window_chars]
    end = stream.consumed + len(text)
    ids = tuple(c.chunk_id for c in stream.chunks if c.end > stream.consumed and c.start < end)
    cycle = CycleState(
        number=state["cycle"].number + 1,
        text=text,
        chunk_ids=ids,
        reason="force_flush" if state["mode"] == "flush" else "checkpoint",
    )
    return {"stream": replace(stream, last_check=monotonic()), "cycle": cycle}


async def fallback_guard(state: EngineState, ctx: Context) -> StatePatch:
    cycle = state["cycle"]
    return {"cycle": replace(cycle, guard="ready" if cycle.text else "idle")}
