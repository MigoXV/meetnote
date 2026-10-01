"""HTTP 契约，与图内部状态分离。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from meetnote.domain.results import CommittedSummary, MeetingLens, MinuteDraft, MinutesPlan
from meetnote.domain.transcripts import TranscriptChunk

SessionStatus = Literal["running", "finalizing", "completed", "failed", "cancelled"]


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=200)


class SessionRow(BaseModel):
    id: str
    title: str
    status: SessionStatus
    accepted_count: int


class SessionSnapshot(SessionRow):
    processed_count: int
    remaining_chars: int
    queue_size: int
    error: str | None
    chunks: list[TranscriptChunk]
    summaries: list[CommittedSummary]
    lens: MeetingLens | None
    plan: MinutesPlan | None
    plan_revision: int
    minutes: MinuteDraft | None
    warnings: list[str]
    debt: float
    event_cursor: int


class ChunkReceipt(BaseModel):
    chunk_id: str
    seq: int
    duplicate: bool


class FinalizeReceipt(BaseModel):
    id: str
    status: SessionStatus


class SampleInfo(BaseModel):
    id: str
    meeting_id: str
    variant: Literal["clean", "colloquial", "asr_noisy"]
    title: str
    segments: int
    characters: int
