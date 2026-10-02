"""HTTP 契约，与图内部状态分离。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from meetnote.domain.results import Fact, MinuteDraft, Revision
from meetnote.domain.transcripts import TranscriptChunk

SessionStatus = Literal["running", "finalizing", "completed", "failed", "cancelled"]


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(default="", max_length=200)
    meeting_date: str = Field(default="", max_length=100)


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
    contract_version: int
    meeting_date: str
    facts: list[Fact]
    revisions: list[Revision]
    sources: list[dict]
    minutes: MinuteDraft | None
    warnings: list[str]
    stats: dict
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
    variant: Literal["clean", "colloquial", "asr_noisy", "formal"]
    title: str
    segments: int
    characters: int
