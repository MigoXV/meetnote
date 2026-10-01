"""图内部按职责分组的状态；每个节点用 replace 返回更新。"""

from dataclasses import dataclass
from typing import Literal, TypedDict

from meetnote.domain.results import (
    BoundaryDecision,
    CommittedSummary,
    MeetingLens,
    MinuteDraft,
    MinutesPlan,
    Signal,
    StructureHint,
)
from meetnote.domain.transcripts import TranscriptChunk


@dataclass(frozen=True)
class StreamState:
    chunks: tuple[TranscriptChunk, ...] = ()
    buffer: str = ""
    cursor: int = 0
    consumed: int = 0
    processed: int = 0
    last_check: float = 0


@dataclass(frozen=True)
class CycleState:
    number: int = 0
    text: str = ""
    chunk_ids: tuple[str, ...] = ()
    reason: str = ""
    signal: Signal | None = None
    decision: BoundaryDecision | None = None
    candidates: tuple[str, ...] = ()
    jobs: tuple[str, ...] = ()
    deferred: tuple[str, ...] = ()
    guard: str = "idle"


@dataclass(frozen=True)
class ScheduleState:
    debt: float = 0
    additions: int = 0
    threshold: float = 3
    mode: str = "watch"
    lens_source_count: int = 0
    hint_source_count: int = 0


@dataclass(frozen=True)
class KnowledgeState:
    summaries: tuple[CommittedSummary, ...] = ()
    keywords: tuple[str, ...] = ()
    open_loops: tuple[str, ...] = ()
    recent_signals: tuple[Signal, ...] = ()
    lens: MeetingLens | None = None
    hint: StructureHint | None = None
    hint_revision: int = 0
    plan: MinutesPlan | None = None
    plan_revision: int = 0
    plan_hint_revision: int = -1
    plan_source_count: int = 0
    carried_notes: tuple[str, ...] = ()
    draft: MinuteDraft | None = None
    warnings: tuple[str, ...] = ()


class GraphState(TypedDict):
    stream: StreamState
    cycle: CycleState
    schedule: ScheduleState
    knowledge: KnowledgeState
    input: TranscriptChunk | None
    mode: Literal["normal", "flush", "compose"]
    title: str


def initial_state(title: str = "") -> GraphState:
    return GraphState(
        stream=StreamState(),
        cycle=CycleState(),
        schedule=ScheduleState(),
        knowledge=KnowledgeState(),
        input=None,
        mode="normal",
        title=title,
    )
