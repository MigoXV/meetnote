"""时钟、原文边界和修订提交不变量。"""

import pytest

from meetnote.config import Settings
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.mock import MockClient


async def test_checkpoint_clock_does_not_require_new_input():
    now = [0.0]
    engine = MeetingEngine(
        Context(Settings(_env_file=None, provider="mock"), MockClient(), lambda *_: None),
        clock=lambda: now[0],
    )
    await engine.append(TranscriptChunk(chunk_id="c", seq=1, text="确认发布。", start=0, end=5))
    assert engine.stream.consumed == 0
    now[0] = 10
    await engine.cycle()
    assert engine.stream.consumed == 5
    calls = engine.context.stats["tasks"]
    now[0] = 20
    await engine.cycle()
    assert engine.context.stats["tasks"] == calls


@pytest.mark.parametrize("text", ["测试。\n\n", "没有标点" * 100, "a b\n中\t文！后半段"])
async def test_segmentation_round_trips_every_character(text):
    engine = MeetingEngine(
        Context(
            Settings(_env_file=None, provider="mock", source_tokens=32),
            MockClient(),
            lambda *_: None,
        )
    )
    chunk = TranscriptChunk(chunk_id="c", seq=1, text=text, start=0, end=len(text))
    sources = list(engine.segment(chunk))
    assert "".join(s.text for s in sources) == text
    for left, right in zip(sources, sources[1:]):
        assert left.end == right.start
    assert all(text[s.start : s.end] == s.text for s in sources)
