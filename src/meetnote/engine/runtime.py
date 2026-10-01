"""所有入口共用的顺序异步流水线与生命周期。"""

from collections.abc import Awaitable, Callable
from time import monotonic, perf_counter

from meetnote.domain.state import CycleMode, EngineState, StatePatch, StreamState, initial_state
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine import ingestion, planning, scheduling, summarization
from meetnote.engine.context import Context

Node = Callable[[EngineState, Context], Awaitable[StatePatch]]

NODES: tuple[Node, ...] = (
    ingestion.bucket_accumulator,
    ingestion.cycle_slice_builder,
    ingestion.fallback_guard,
    scheduling.llm_signal_marker,
    scheduling.commit_debt_meter,
    scheduling.work_candidate_builder,
    scheduling.model_work_scheduler,
    planning.meeting_lens_builder,
    planning.template_planner,
    summarization.summary_worker,
    summarization.bucket_enricher,
    planning.main_minutes_planner,
    planning.minutes_composer,
)


class NoProgress(RuntimeError):
    pass


class MeetingEngine:
    def __init__(self, context: Context, title: str = ""):
        self.context = context
        self.state = initial_state(title)
        self.state["stream"] = StreamState(last_check=monotonic())

    async def _execute(self, node: Node, state: EngineState) -> EngineState:
        name = node.__name__
        started = perf_counter()
        self.context.emit("node.started", {"node": name})
        try:
            patch = await node(state, self.context)
        except Exception as exc:
            self.context.emit("node.failed", {"node": name, "error": type(exc).__name__})
            raise
        # 每步成功后立即保存；后续失败或取消时仍保留已完成的更新。
        self.state = {**state, **patch}
        self.context.emit(
            "node.completed" if patch else "node.skipped",
            {"node": name, "elapsed_ms": round((perf_counter() - started) * 1000)},
        )
        return self.state

    async def cycle(self, mode: CycleMode = "normal", chunk: TranscriptChunk | None = None) -> None:
        state: EngineState = {**self.state, "mode": mode, "input": chunk}
        for node in NODES:
            state = await self._execute(node, state)

    async def append(self, chunk: TranscriptChunk) -> None:
        await self.cycle(chunk=chunk)

    async def finalize(self) -> None:
        while self.state["stream"].buffer:
            before = len(self.state["stream"].buffer)
            await self.cycle("flush")
            if len(self.state["stream"].buffer) >= before:
                raise NoProgress("final_flush_made_no_progress")
        await self.cycle("compose")
