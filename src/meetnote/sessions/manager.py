from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass, field
from time import monotonic
from uuid import uuid4

from meetnote.config import Settings
from meetnote.domain.transcripts import ChunkInput, TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ModelClient
from meetnote.sessions.events import EventLog

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}


class SessionError(RuntimeError):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)


@dataclass
class Session:
    id: str
    title: str
    events: EventLog
    queue: asyncio.Queue[TranscriptChunk]
    engine: MeetingEngine
    status: str = "running"
    accepted: dict[str, TranscriptChunk] = field(default_factory=dict)
    chars: int = 0
    last_input: float = field(default_factory=monotonic)
    terminal_at: float | None = None
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    error: str | None = None

    def snapshot(self) -> dict:
        state = self.engine.state
        knowledge, stream = state["knowledge"], state["stream"]
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "accepted_count": len(self.accepted),
            "processed_count": stream.processed,
            "remaining_chars": self.chars - stream.consumed,
            "queue_size": self.queue.qsize(),
            "error": self.error,
            "chunks": [c.model_dump(mode="json") for c in self.accepted.values()],
            "summaries": [s.model_dump(mode="json") for s in knowledge.summaries],
            "lens": knowledge.lens.model_dump() if knowledge.lens else None,
            "plan": knowledge.plan.model_dump() if knowledge.plan else None,
            "plan_revision": knowledge.plan_revision,
            "minutes": knowledge.draft.model_dump() if knowledge.draft else None,
            "warnings": list(knowledge.warnings),
            "debt": state["schedule"].debt,
            "event_cursor": self.events.sequence,
        }


class SessionManager:
    def __init__(self, settings: Settings, model: ModelClient):
        self.settings, self.model = settings, model
        self.sessions: dict[str, Session] = {}
        self.closed = False
        self.janitor: asyncio.Task | None = None

    def start(self) -> None:
        self.janitor = asyncio.create_task(self._cleanup_loop())

    def create(self, title: str = "") -> Session:
        if self.closed:
            raise SessionError(503, "service_stopping")
        # 包括短期保留的终态会话，防止快速创建/结束绕开内存上限。
        if len(self.sessions) >= self.settings.max_sessions:
            raise SessionError(429, "session_limit")
        events = EventLog(self.settings.event_count, self.settings.event_bytes)
        engine = MeetingEngine(Context(self.settings, self.model, events.publish), title)
        session = Session(
            uuid4().hex, title, events, asyncio.Queue(self.settings.queue_size), engine
        )
        self.sessions[session.id] = session
        session.task = asyncio.create_task(self._worker(session), name=f"meeting-{session.id}")
        events.publish("session.created", {"id": session.id, "title": title})
        return session

    def get(self, session_id: str) -> Session:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise SessionError(404, "session_not_found") from exc

    async def append(self, session_id: str, request: ChunkInput) -> dict:
        session = self.get(session_id)
        async with session.lock:
            if request.chunk_id and request.chunk_id in session.accepted:
                old = session.accepted[request.chunk_id]
                if (old.text, old.speaker, old.timestamp) != (
                    request.text,
                    request.speaker,
                    request.timestamp,
                ):
                    raise SessionError(409, "chunk_id_conflict")
                return {"chunk_id": old.chunk_id, "seq": old.seq, "duplicate": True}
            if session.status != "running" or self.closed:
                raise SessionError(409, "session_not_accepting")
            if session.queue.full():
                raise SessionError(429, "input_queue_full")
            if session.chars + len(request.text) > self.settings.max_session_chars:
                raise SessionError(413, "session_text_limit")
            chunk = TranscriptChunk(
                **request.model_dump(exclude={"chunk_id"}),
                chunk_id=request.chunk_id or uuid4().hex,
                seq=len(session.accepted) + 1,
                start=session.chars,
                end=session.chars + len(request.text),
            )
            session.queue.put_nowait(chunk)
            session.accepted[chunk.chunk_id] = chunk
            session.chars = chunk.end
            session.last_input = monotonic()
            session.events.publish("input.accepted", chunk.model_dump(mode="json"))
            session.wake.set()
            return {"chunk_id": chunk.chunk_id, "seq": chunk.seq, "duplicate": False}

    async def finalize(self, session_id: str) -> Session:
        session = self.get(session_id)
        async with session.lock:
            if session.status == "running":
                session.status = "finalizing"
                session.events.publish("session.finalizing", {"id": session.id})
                session.wake.set()
        return session

    async def cancel(self, session: Session, reason: str = "cancelled") -> None:
        async with session.lock:
            if session.status in TERMINAL:
                return
            session.status = "cancelled"
            session.error = reason
            session.terminal_at = monotonic()
            if session.task:
                session.task.cancel()
        if session.task:
            with suppress(asyncio.CancelledError):
                await session.task
        session.events.publish("session.cancelled", {"id": session.id, "reason": reason})

    async def delete(self, session_id: str) -> None:
        session = self.get(session_id)
        await self.cancel(session)
        self.sessions.pop(session_id, None)
        session.events.publish("session.deleted", {"id": session_id})

    async def _worker(self, session: Session) -> None:
        try:
            while True:
                if not session.queue.empty():
                    chunk = session.queue.get_nowait()
                    try:
                        await session.engine.append(chunk)
                    finally:
                        session.queue.task_done()
                    session.events.publish("input.processed", {"seq": chunk.seq})
                    continue
                if session.status == "finalizing":
                    break
                session.wake.clear()
                # 定时检查点不能依赖下一个输入包到来。
                try:
                    await asyncio.wait_for(
                        session.wake.wait(), max(self.settings.checkpoint_seconds, 0.05)
                    )
                except asyncio.TimeoutError:
                    if session.engine.state["stream"].buffer:
                        await session.engine.cycle()
            await session.engine.finalize()
            session.status = "completed"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            session.status = "failed"
            session.error = (
                str(exc)
                if type(exc).__name__ in {"ModelFailure", "NoProgress"}
                else type(exc).__name__
            )
            logger.warning("session failed id=%s category=%s", session.id, type(exc).__name__)
        finally:
            if session.status in TERMINAL:
                session.terminal_at = monotonic()
        session.events.publish(
            "session.terminal",
            {
                "id": session.id,
                "status": session.status,
                "error": session.error,
            },
        )

    async def cleanup(self, now: float | None = None) -> None:
        now = monotonic() if now is None else now
        for session in list(self.sessions.values()):
            if (
                session.status == "running"
                and now - session.last_input > self.settings.idle_seconds
            ):
                await self.cancel(session, "idle_timeout")
            if (
                session.terminal_at is not None
                and now - session.terminal_at > self.settings.retention_seconds
            ):
                self.sessions.pop(session.id, None)

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(
                min(15, self.settings.retention_seconds, self.settings.idle_seconds)
            )
            await self.cleanup()

    async def close(self) -> None:
        self.closed = True
        if self.janitor:
            self.janitor.cancel()
            with suppress(asyncio.CancelledError):
                await self.janitor
        await asyncio.gather(
            *(self.cancel(s, "service_stopping") for s in list(self.sessions.values()))
        )
        self.sessions.clear()
        await self.model.close()
