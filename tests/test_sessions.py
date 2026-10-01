import asyncio

import pytest

from meetnote.config import Settings
from meetnote.domain.transcripts import ChunkInput
from meetnote.llm.mock import MockClient
from meetnote.sessions.events import EventLog
from meetnote.sessions.manager import SessionError, SessionManager


def manager(**kwargs):
    return SessionManager(Settings(_env_file=None, provider="mock", **kwargs), MockClient())


async def test_finalize_rejects_new_chunks_and_drains_accepted():
    service = manager()
    session = service.create()
    await service.append(session.id, ChunkInput(text="定稿文字必须被处理", chunk_id="a"))
    await service.finalize(session.id)
    with pytest.raises(SessionError, match="session_not_accepting"):
        await service.append(session.id, ChunkInput(text="收尾后的新输入"))
    await session.task
    assert session.status == "completed"
    assert session.snapshot()["processed_count"] == 1
    assert session.snapshot()["remaining_chars"] == 0
    await service.close()


async def test_concurrent_append_finalize_has_no_false_acceptance():
    service = manager()
    session = service.create()
    results = await asyncio.gather(
        *(service.append(session.id, ChunkInput(text=f"第{i}条唯一发言内容。")) for i in range(8)),
        service.finalize(session.id),
        *(service.append(session.id, ChunkInput(text=f"结束后{i}发言")) for i in range(8)),
        return_exceptions=True,
    )
    await session.task
    accepted = sum(isinstance(result, dict) for result in results)
    assert session.snapshot()["accepted_count"] == accepted
    assert session.snapshot()["processed_count"] == accepted
    assert session.status == "completed"
    await service.close()


async def test_idempotency_conflict_and_queue_backpressure():
    service = manager(queue_size=1)
    session = service.create()
    request = ChunkInput(chunk_id="fixed", text="原文")
    first = await service.append(session.id, request)
    second = await service.append(session.id, request)
    assert first["seq"] == second["seq"] == 1
    assert second["duplicate"]
    with pytest.raises(SessionError, match="chunk_id_conflict"):
        await service.append(session.id, ChunkInput(chunk_id="fixed", text="不同内容"))
    with pytest.raises(SessionError, match="input_queue_full"):
        await service.append(session.id, ChunkInput(text="下一个"))
    assert len(session.accepted) == 1
    await service.close()


async def test_cleanup_limits_and_restart():
    service = manager(max_sessions=1, idle_seconds=1, retention_seconds=1)
    session = service.create()
    with pytest.raises(SessionError, match="session_limit"):
        service.create()
    await service.cleanup(now=session.last_input + 2)
    assert session.status == "cancelled"
    await service.cleanup(now=session.terminal_at + 2)
    with pytest.raises(SessionError, match="session_not_found"):
        service.get(session.id)
    await service.close()
    restarted = manager()
    assert not restarted.sessions
    await restarted.close()


def test_bounded_events_and_expired_cursor():
    log = EventLog(3, 1024)
    for i in range(10):
        log.publish("input", {"text": "a" * 100, "seq": i})
    assert log.size <= 1024
    assert len(log.items) == 3
    assert log.after(0) is None
    assert [e.id for e in log.after(8)] == [9, 10]
    log.publish("large", {"text": "a" * 3000})
    assert log.items[-1][0].data["truncated"]
    assert log.size <= 1024


async def test_service_does_not_write_meeting_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    service = manager()
    session = service.create()
    await service.append(session.id, ChunkInput(text="王宁周四完成验收。"))
    await service.finalize(session.id)
    await session.task
    await service.close()
    assert list(tmp_path.iterdir()) == []


async def test_idle_checkpoint_runs_without_next_input():
    service = manager(min_chars=5, window_chars=700, checkpoint_seconds=0.01)
    session = service.create()
    await service.append(session.id, ChunkInput(text="周四完成验收，陈宁负责。"))
    for _ in range(100):
        if session.engine.state["knowledge"].summaries:
            break
        await asyncio.sleep(0.01)
    assert session.engine.state["knowledge"].summaries
    assert session.status == "running"
    await service.close()
