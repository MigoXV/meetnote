from dataclasses import replace

from meetnote.domain.results import MeetingLens, MinuteDraft, MinutesPlan, Section, StructureHint
from meetnote.domain.state import GraphState, KnowledgeState
from meetnote.engine.context import Context
from meetnote.engine.quality import keyword_candidates, merge_unique
from meetnote.llm.client import ModelFailure


def knowledge_context(knowledge: KnowledgeState) -> dict:
    return {
        "summaries": [s.model_dump(mode="json") for s in knowledge.summaries[-16:]],
        "keywords": list(knowledge.keywords),
        "open_loops": list(knowledge.open_loops),
        "lens": knowledge.lens.model_dump() if knowledge.lens else None,
        "hint": knowledge.hint.model_dump() if knowledge.hint else None,
        "recent_signals": [s.model_dump() for s in knowledge.recent_signals],
        "previous_plan": knowledge.plan.model_dump() if knowledge.plan else None,
        "carried_notes": list(knowledge.carried_notes),
    }


def degraded(ctx: Context, node: str, exc: Exception, knowledge: KnowledgeState) -> KnowledgeState:
    warning = f"{node}: {exc}，使用规则降级结果"
    ctx.emit("node.degraded", {"node": node, "reason": str(exc)})
    return replace(
        knowledge, warnings=tuple(merge_unique(list(knowledge.warnings), [warning])[-20:])
    )


async def meeting_lens_builder(state: GraphState, ctx: Context) -> dict:
    if "lens" not in state["cycle"].jobs:
        return {}
    knowledge = state["knowledge"]
    try:
        lens = await ctx.invoke("lens", knowledge_context(knowledge), MeetingLens)
    except ModelFailure as exc:
        knowledge = degraded(ctx, "meeting_lens_builder", exc, knowledge)
        text = "\n".join(s.summary for s in knowledge.summaries[-4:])
        lens = MeetingLens(
            orientation=keyword_candidates(text)[:4] or ["阶段性同步"],
            organization_axis="problem"
            if any(w in text for w in ("问题", "风险", "异常", "故障", "修复"))
            else "topic",
            confidence=0.45,
            unstable_parts=["规则降级，方向待确认"],
        )
    return {
        "knowledge": replace(knowledge, lens=lens),
        "schedule": replace(state["schedule"], lens_source_count=len(knowledge.summaries)),
    }


def fallback_hint(knowledge: KnowledgeState) -> StructureHint:
    return StructureHint(
        body_axis_hint=knowledge.lens.organization_axis if knowledge.lens else "topic",
        section_candidates=list(knowledge.keywords[:4]) or ["主要议题", "关键事实", "待确认项"],
        evidence_policy_draft="仅采用有摘要与原文支持的事实；风险与缺失信息保留待确认。",
    )


async def template_planner(state: GraphState, ctx: Context) -> dict:
    if "hint" not in state["cycle"].jobs:
        return {}
    knowledge = state["knowledge"]
    try:
        hint = await ctx.invoke("hint", knowledge_context(knowledge), StructureHint)
    except ModelFailure as exc:
        knowledge = degraded(ctx, "template_planner", exc, knowledge)
        hint = fallback_hint(knowledge)
    return {
        "knowledge": replace(knowledge, hint=hint, hint_revision=knowledge.hint_revision + 1),
        "schedule": replace(state["schedule"], hint_source_count=len(knowledge.summaries)),
    }


async def main_minutes_planner(state: GraphState, ctx: Context) -> dict:
    knowledge = state["knowledge"]
    final = state["mode"] == "compose"
    if not knowledge.summaries or (
        not final
        and (knowledge.hint is None or knowledge.plan_hint_revision == knowledge.hint_revision)
    ):
        return {}
    hint = knowledge.hint or fallback_hint(knowledge)
    context = knowledge_context(knowledge)
    # 最终规划要覆盖整场已提交证据，不能只看最近几段。
    if final:
        context["summaries"] = [s.model_dump(mode="json") for s in knowledge.summaries]
    context["hint"] = hint.model_dump()
    try:
        plan = await ctx.invoke("plan", context, MinutesPlan)
        if not plan.section_order or not plan.composer_brief:
            raise ModelFailure("empty_minutes_plan")
    except ModelFailure as exc:
        knowledge = degraded(ctx, "main_minutes_planner", exc, knowledge)
        plan = MinutesPlan(
            body_axis=hint.body_axis_hint,
            section_order=hint.section_candidates,
            key_topics=list(knowledge.keywords[:8]),
            evidence_policy=hint.evidence_policy_draft,
            composer_brief="依据全部已提交摘要生成会议纪要，不补充未知负责人和期限。",
            uncertainty_policy="ASR 风险与未闭合问题进入待确认。",
        )
    revision = knowledge.plan_revision + 1
    ctx.emit("plan.updated", {"revision": revision, "plan": plan.model_dump()})
    notes = merge_unique(
        list(knowledge.carried_notes), [plan.uncertainty_policy, hint.pending_structure_reason]
    )[-8:]
    return {
        "knowledge": replace(
            knowledge,
            plan=plan,
            plan_revision=revision,
            plan_hint_revision=knowledge.hint_revision,
            plan_source_count=len(knowledge.summaries),
            carried_notes=tuple(notes),
        )
    }


async def minutes_composer(state: GraphState, ctx: Context) -> dict:
    knowledge = state["knowledge"]
    if state["mode"] != "compose" or not knowledge.plan:
        return {}
    context = knowledge_context(knowledge)
    context.update(
        title=state["title"],
        plan=knowledge.plan.model_dump(),
        summaries=[s.model_dump(mode="json") for s in knowledge.summaries],
    )
    evidence_ids = [s.id for s in knowledge.summaries]
    try:
        draft = await ctx.invoke("compose", context, MinuteDraft)
        if not draft.markdown.strip():
            raise ModelFailure("empty_minutes")
        if any(item not in evidence_ids for item in draft.evidence_trace):
            raise ModelFailure("unknown_evidence_reference")
    except ModelFailure as exc:
        knowledge = degraded(ctx, "minutes_composer", exc, knowledge)
        sections = [
            Section(title="会议要点", items=[s.summary for s in knowledge.summaries]),
            Section(title="待确认与风险", items=list(knowledge.open_loops)),
        ]
        draft = MinuteDraft(
            title=state["title"] or "会议纪要",
            sections=sections,
            markdown="\n\n".join(
                "## " + section.title + "\n" + "\n".join("- " + item for item in section.items)
                for section in sections
                if section.items
            ),
            evidence_trace=evidence_ids,
        )
    warnings = merge_unique(draft.delivery_warnings, list(knowledge.warnings))
    draft = draft.model_copy(update={"delivery_warnings": warnings})
    ctx.emit("minutes.completed", draft.model_dump(mode="json"))
    return {"knowledge": replace(knowledge, draft=draft)}
