"""证据、事实修订与交付契约；字段说明同时进入模型 Schema。"""

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model


class Result(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactContent(Result):
    text: str = Field(
        min_length=1, description="完整保留数字、单位、否定、适用范围及不确定性的一条事实"
    )
    topic: str = Field(min_length=1, description="简短议题")
    subject: str = Field(
        min_length=1,
        description="具体对象及属性，如采购预算、采购数量、交付日期；省略各条共有的项目名，不复述整句",
    )
    owner: str = Field(
        default="",
        description="已确认的行动、执行决定或执行约束可填明示执行负责人；建议提出者不是执行负责人",
    )
    due: str = Field(
        default="",
        description="原文任务期限、活动安排时间或约定时长，未知为空；没有日期依据不补年份",
    )
    source_ids: list[str] = Field(min_length=1, description="支持整条事实的输入片段 ID")
    owner_source_ids: list[str] = Field(
        default_factory=list,
        description="owner 非空时必须填写其明确分工证据 ID；owner 为空时为空列表",
    )
    due_source_ids: list[str] = Field(
        default_factory=list, description="due 非空时必须填写期限证据 ID；due 为空时为空列表"
    )


class FactInput(FactContent):
    kind: Literal["fact", "proposal", "decision", "action", "constraint", "question", "risk"]
    status: Literal["confirmed", "proposed", "unresolved"]


CATEGORIES = {
    "已确认事实": ("fact", "confirmed"),
    "未确认信息": ("fact", "unresolved"),
    "待批准建议": ("proposal", "proposed"),
    "已定决定": ("decision", "confirmed"),
    "已分配行动": ("action", "confirmed"),
    "已定约束": ("constraint", "confirmed"),
    "未决问题": ("question", "unresolved"),
    "风险": ("risk", "unresolved"),
}


class Classification(Result):
    category: Literal[tuple(CATEGORIES)] = Field(
        description="明确‘某事由某人负责’或点名执行是已分配行动，不选已确认事实。‘我会…’‘我还会…’‘我准备材料时会…’也是已分配行动，执行人对应 speaker。取消建议选已定决定；只有尚未通过的建议选待批准建议。已确认事实不填写执行负责人。"
    )


class ExtractedFact(FactContent, Classification):
    pass


class IgnoredSource(Result):
    source_id: str
    reason: str = Field(min_length=1, description="仅寒暄、无信息重复等无事实内容可忽略，说明依据")


class Extraction(Result):
    facts: list[FactInput]
    ignored: list[IgnoredSource] = Field(default_factory=list, max_length=32)


class SourceExtraction(Result):
    facts: list[ExtractedFact] = Field(
        default_factory=list,
        max_length=48,
        description="同一命题只选一种分类记录一次；不同状态、属性或负责人的事项分别成条",
    )
    ignored_reason: str = Field(default="", description="仅无事实内容可填写忽略原因，否则留空")


def extraction_schema(source_ids):
    """每个原文 ID 都是必填键，避免自由数组跳过输入片段。"""
    return create_model(
        "SourceExtractionBatch",
        __base__=Result,
        **{sid: (SourceExtraction, ...) for sid in source_ids},
    )


def unpack_extraction(result):
    facts, ignored, seen = [], [], set()
    for sid, record in result.model_dump().items():
        for value in record["facts"]:
            kind, status = CATEGORIES[value.pop("category")]
            value.update(kind=kind, status=status)
            fact = FactInput.model_validate(value)
            key = fact.model_dump_json()
            if key not in seen:
                facts.append(fact)
                seen.add(key)
        if record["ignored_reason"]:
            ignored.append(IgnoredSource(source_id=sid, reason=record["ignored_reason"]))
    return Extraction(facts=facts, ignored=ignored)


class Fact(FactInput):
    id: str
    revision: int = 1
    status: Literal[
        "confirmed", "proposed", "unresolved", "conflicted", "superseded", "retracted", "resolved"
    ]
    related_ids: list[str] = Field(default_factory=list)


class Revision(Result):
    fact_id: str
    revision: int
    before: Fact
    after: Fact
    reason: str
    source_ids: list[str]


class Relation(Result):
    new_id: str
    old_id: str
    kind: Literal["supersedes", "retracts", "resolves", "conflicts"]
    source_ids: list[str] = Field(
        min_length=1, description="必须包含新事实证据；修订/解决关系必须有明确原文依据"
    )
    reason: str = Field(min_length=1)


class RelationDecision(Result):
    kind: Literal["none", "supersedes", "retracts", "resolves", "conflicts"]
    evidence_id: str | None
    reason: str = Field(max_length=200)


def relation_schema(choices, kinds):
    fields = {}
    none = create_model(
        "NoRelation",
        __base__=RelationDecision,
        kind=(Literal["none"], ...),
        evidence_id=(type(None), ...),
    )
    for key, ids in choices.items():
        item = create_model(
            "Relation_" + key,
            __base__=RelationDecision,
            kind=(Literal[tuple(k for k in kinds[key] if k != "none")], ...),
            evidence_id=(Literal[tuple(ids)], ...),
        )
        fields[key] = (none | item, ...)
    return create_model("RelationBatch", __base__=Result, **fields)


class SectionItem(Result):
    text: str = Field(min_length=1)
    fact_ids: list[str] = Field(min_length=1)


class FieldSupport(Result):
    owner_supported: bool = Field(description="引用原文明示此人执行这条事项；发言或建议不算分工")
    due_supported: bool = Field(
        description="任务期限、活动安排时间、约定时长均可；日期须直接约束该事项，别猜、不推测、不讨论等讨论时间不算任务期限"
    )
    reason: str = Field(max_length=240, description="分别指出负责人和期限的原文依据或缺失")


def field_support_schema(ids):
    return create_model(
        "FieldSupportBatch", __base__=Result, **{fid: (FieldSupport, ...) for fid in ids}
    )


class Chapter(Result):
    title: str = Field(min_length=1)
    items: list[SectionItem] = Field(min_length=1)


class ChapterSelection(Result):
    fact_ids: list[str] = Field(
        min_length=1, max_length=6, description="选择本议题最重要的至多六条事实 ID，不生成新正文"
    )


def chapter_selection_schema(limit):
    return create_model(
        "ChapterSelection",
        __base__=Result,
        fact_ids=(list[str], Field(min_length=1, max_length=min(6, limit))),
    )


class OverviewParagraph(SectionItem):
    text: str = Field(
        min_length=1, max_length=100, description="只交代主题和总体发现，不重复列举结论"
    )


class ReaderMinutes(Result):
    overview: OverviewParagraph = Field(description="会议主题与核心结果，一段连贯概述")
    conclusions: list[SectionItem] = Field(
        max_length=3, description="主要发现、结论或决议；无则空数组"
    )
    discussion: list[SectionItem] = Field(description="按主题归纳的讨论要点，避免与结论重复")
    actions: list[SectionItem] = Field(description="实际分工及提出的后续安排，明确区分建议与承诺")
    open_questions: list[SectionItem] = Field(
        max_length=2, description="尚需解决的实质问题；无则空数组"
    )


def reader_minutes_schema(fact_ids):
    """把已有引用边界前移到结构化解码；输出字段与公开纪要类型保持一致。"""
    ids = tuple(dict.fromkeys(fact_ids))
    if not ids:
        raise ValueError("reader minutes require allowed facts")
    reference = Literal[ids]
    paragraph = create_model(
        "GroundedParagraph",
        __base__=SectionItem,
        fact_ids=(list[reference], deepcopy(SectionItem.model_fields["fact_ids"])),
    )
    overview = create_model(
        "GroundedOverview",
        __base__=OverviewParagraph,
        fact_ids=(list[reference], deepcopy(SectionItem.model_fields["fact_ids"])),
    )
    return create_model(
        "GroundedReaderMinutes",
        __base__=ReaderMinutes,
        **{
            name: (overview if name == "overview" else list[paragraph], deepcopy(field))
            for name, field in ReaderMinutes.model_fields.items()
        },
    )


class MinuteDraft(Result):
    title: str
    narrative: ReaderMinutes | None = None
    markdown: str
    sections: list[Chapter]
    checklist: list[Fact]
    evidence_trace: list[str]
    delivery_warnings: list[str] = Field(default_factory=list)
    coverage: dict[str, int] = Field(default_factory=dict)
