"""所有入口共用的图与生命周期；没有旁路节点执行列表。"""

from time import monotonic, perf_counter

from langgraph.graph import END, START, StateGraph

from meetnote.domain.state import GraphState, StreamState, initial_state
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine import ingestion, planning, scheduling, summarization
from meetnote.engine.context import Context

NODES = (
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
        builder = StateGraph(GraphState)
        previous = START
        for node in NODES:
            builder.add_node(node.__name__, self._wrap(node))
            builder.add_edge(previous, node.__name__)
            previous = node.__name__
        builder.add_edge(previous, END)
        self.graph = builder.compile()

    def _wrap(self, node):
        async def execute(state: GraphState):
            name = node.__name__
            started = perf_counter()
            self.context.emit("node.started", {"node": name})
            try:
                patch = await node(state, self.context)
            except Exception as exc:
                self.context.emit("node.failed", {"node": name, "error": type(exc).__name__})
                raise
            # 保存已成功执行的节点状态，模型失败时原文仍可查询。
            self.state = {**state, **patch}
            self.context.emit(
                "node.completed" if patch else "node.skipped",
                {"node": name, "elapsed_ms": round((perf_counter() - started) * 1000)},
            )
            return patch

        return execute

    async def cycle(self, mode="normal", chunk: TranscriptChunk | None = None) -> None:
        invocation = {**self.state, "mode": mode, "input": chunk}
        self.state = await self.graph.ainvoke(invocation)

    async def append(self, chunk: TranscriptChunk) -> None:
        await self.cycle(chunk=chunk)

    async def finalize(self) -> None:
        while self.state["stream"].buffer:
            before = len(self.state["stream"].buffer)
            await self.cycle("flush")
            if len(self.state["stream"].buffer) >= before:
                raise NoProgress("final_flush_made_no_progress")
        await self.cycle("compose")
