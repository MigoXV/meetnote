from dataclasses import replace

from meetnote.domain.results import Signal
from meetnote.domain.state import EngineState, StatePatch
from meetnote.engine.context import Context
from meetnote.llm.client import ModelFailure


async def llm_signal_marker(state: EngineState, ctx: Context) -> StatePatch:
    cycle = state["cycle"]
    if not cycle.text:
        return {}
    try:
        signal = await ctx.invoke("marker", {"text": cycle.text, "reason": cycle.reason}, Signal)
    except ModelFailure as exc:
        ctx.emit("node.degraded", {"node": "llm_signal_marker", "reason": str(exc)})
        points = 2 if len(cycle.text) >= ctx.settings.min_chars else 1
        points += 0.5 if cycle.text[-1:] in "。！？；.!?;" else 0
        signal = Signal(
            semantic_hints=[cycle.text[:120]],
            commit_points=min(points, 3),
            commit_level="mid",
            risk_if_skip="medium",
        )
    if state["mode"] == "flush":
        signal = signal.model_copy(update={"commit_points": 3, "commit_level": "force"})
    elif signal.commit_level == "force":
        signal = signal.model_copy(update={"commit_level": "high"})
    return {"cycle": replace(cycle, signal=signal)}


async def commit_debt_meter(state: EngineState, ctx: Context) -> StatePatch:
    signal = state["cycle"].signal
    debt = state["schedule"]
    if signal is None:
        return {}
    points = debt.debt + signal.commit_points
    mode = (
        "force"
        if signal.commit_level == "force"
        else ("stage2" if points >= debt.threshold else "watch")
    )
    return {
        "schedule": replace(
            debt, debt=points, additions=debt.additions + (signal.commit_points > 0), mode=mode
        )
    }


async def work_candidate_builder(state: EngineState, ctx: Context) -> StatePatch:
    cycle, debt, knowledge = state["cycle"], state["schedule"], state["knowledge"]
    candidates: tuple[str, ...] = ()
    count = len(knowledge.summaries)
    lens = knowledge.lens
    if cycle.text:
        if debt.mode in {"stage2", "force"}:
            candidates = ("summary",)
        elif count >= 2:
            if (
                lens is None
                or (lens.confidence < 0.55 and count > debt.lens_source_count)
                or count - debt.lens_source_count >= 3
            ):
                candidates = ("lens",)
            elif lens.confidence >= 0.55 and (
                knowledge.hint is None or count - debt.hint_source_count >= 2
            ):
                candidates = ("hint",)
    return {"cycle": replace(cycle, candidates=candidates)}


async def model_work_scheduler(state: EngineState, ctx: Context) -> StatePatch:
    cycle = state["cycle"]
    jobs = ("summary",) if "summary" in cycle.candidates else cycle.candidates
    deferred = tuple(item for item in cycle.candidates if item not in jobs)
    ctx.emit(
        "scheduler.updated",
        {
            "debt": state["schedule"].debt,
            "threshold": state["schedule"].threshold,
            "jobs": list(jobs),
            "deferred": list(deferred),
            "reason": "summary_priority" if "summary" in jobs else "idle_window",
        },
    )
    return {"cycle": replace(cycle, jobs=jobs, deferred=deferred)}
