from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ChunkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=16000)
    chunk_id: str | None = Field(default=None, min_length=1, max_length=128)
    speaker: str | None = Field(default=None, max_length=200)
    timestamp: str | None = Field(default=None, max_length=100)

    @field_validator("text")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("文本不能为空白")
        return value


class TranscriptChunk(ChunkInput):
    chunk_id: str
    seq: int
    start: int
    end: int


class Segment(BaseModel):
    id: str
    speaker: str
    text: str = Field(min_length=1)


class Transcript(BaseModel):
    id: str
    meeting_id: str
    variant: Literal["clean", "colloquial", "asr_noisy"]
    title: str
    meeting_date: str
    segments: list[Segment] = Field(min_length=1)
