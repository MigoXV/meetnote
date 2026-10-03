"""仅由会话消费者写入的内存状态。"""

from dataclasses import dataclass, field

from meetnote.domain.results import Fact, MinuteDraft, Revision
from meetnote.domain.transcripts import TranscriptChunk


@dataclass(frozen=True)
class Source:
    id: str
    chunk_id: str
    text: str
    speaker: str | None
    timestamp: str | None
    start: int
    end: int

    def payload(self):
        return {
            "id": self.id,
            "text": self.text,
            "speaker": self.speaker,
            "timestamp": self.timestamp,
        }


@dataclass
class StreamState:
    chunks: list[TranscriptChunk] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    pending: list[Source] = field(default_factory=list)
    dispositions: dict[str, str] = field(default_factory=dict)
    cursor: int = 0
    consumed: int = 0
    processed: int = 0
    last_check: float = 0

    @property
    def buffer(self):
        return "".join(s.text for s in self.pending)


@dataclass
class KnowledgeState:
    facts: list[Fact] = field(default_factory=list)
    revisions: list[Revision] = field(default_factory=list)
    draft: MinuteDraft | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def summaries(self):
        return [f for f in self.facts if f.status not in {"superseded", "retracted", "resolved"}]
