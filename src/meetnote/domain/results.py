"""模型输出契约；业务字段说明同时进入 JSON Schema。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Action(Result):
    owner: str = Field(
        description="仅限原文明示分工或本人承诺的负责人；发言人不自动是负责人，未知时为空"
    )
    task: str = Field(description="已确认的行动，不把建议当作承诺")
    due_date: str = Field(default="", description="原文给出的期限；未知时为空")


class SummaryAtom(Result):
    summary: str = Field(min_length=1, description="一到三句话概括已闭合内容")
    key_points: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(
        default_factory=list, description="简短专名或主题，不能写完整事实句"
    )
    decisions: list[str] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class BoundaryDecision(Result):
    should_emit: bool
    meeting_end: bool = False
    summary_end_anchor: str = Field(
        default="", description="已摘要前缀的最后一段逐字原文，足够长以唯一定位"
    )
    summary_atoms: list[SummaryAtom] = Field(default_factory=list)
    remaining_text: str = Field(
        default="", description="尚未摘要部分开头20到80字，逐字复制；已全部摘要时为空"
    )
    carry_context_note: str = ""
    reason: str = ""


class Signal(Result):
    semantic_hints: list[str] = Field(default_factory=list)
    asr_risks: list[str] = Field(default_factory=list)
    open_slots: list[str] = Field(default_factory=list)
    commit_level: Literal["none", "low", "mid", "high", "force"] = Field(
        default="none", description="none、low、mid、high 或 force"
    )
    commit_points: float = Field(default=0, ge=0, le=3)
    risk_if_skip: Literal["low", "medium", "high"] = "low"
    force_commit_if: list[str] = Field(default_factory=list)


class MeetingLens(Result):
    orientation: list[str] = Field(default_factory=list)
    organization_axis: str = "topic"
    confidence: float = Field(default=0, ge=0, le=1)
    unstable_parts: list[str] = Field(default_factory=list)


class StructureHint(Result):
    body_axis_hint: str = "topic"
    section_candidates: list[str] = Field(default_factory=list)
    evidence_policy_draft: str = ""
    pending_structure_reason: str = ""


class MinutesPlan(Result):
    body_axis: str = "topic"
    section_order: list[str] = Field(default_factory=list)
    key_topics: list[str] = Field(default_factory=list)
    content_promotion_policy: str = ""
    evidence_policy: str = ""
    composer_brief: str = ""
    uncertainty_policy: str = ""


class Section(Result):
    title: str
    items: list[str]


class MinuteDraft(Result):
    title: str = "会议纪要"
    markdown: str = Field(min_length=1)
    sections: list[Section] = Field(default_factory=list)
    evidence_trace: list[str] = Field(
        default_factory=list, description="仅引用输入提供的摘要证据ID"
    )
    delivery_warnings: list[str] = Field(default_factory=list)


class CommittedSummary(SummaryAtom):
    id: str
    chunk_ids: list[str]
    start: int
    end: int
