import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from meetnote.config import Settings
from meetnote.corpus.reader import read_transcripts
from meetnote.domain.transcripts import ChunkInput, Transcript
from meetnote.engine.models import create_model
from meetnote.engine.runtime import STAGES
from meetnote.llm.client import ModelClient
from meetnote.sessions.events import Event
from meetnote.sessions.manager import TERMINAL, SessionError, SessionManager
from meetnote.web.schemas import (
    ChunkReceipt,
    CreateSession,
    FinalizeReceipt,
    SampleInfo,
    SessionRow,
    SessionSnapshot,
)

WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def create_app(
    settings: Settings | None = None,
    *,
    model: ModelClient | None = None,
    frontend: bool = True,
    dist: Path = WEB_DIST,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = model or (create_model(settings))
        manager = SessionManager(settings, client)
        app.state.manager = manager
        manager.start()
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(title="MeetNote", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(SessionError)
    async def session_error(request: Request, exc: SessionError):
        return JSONResponse(status_code=exc.status, content={"detail": exc.code})

    def manager() -> SessionManager:
        return app.state.manager

    @app.get("/health/live")
    async def live():
        return {"status": "ok"}

    @app.get("/api/v1/info")
    async def info():
        return {
            "name": "MeetNote",
            "contract_version": 2,
            "capabilities": ["rolling_minutes", "minutes_preview"],
            "provider": settings.provider,
            "model": settings.model,
            "nodes": list(STAGES),
            "persistence": False,
            "retention_seconds": settings.retention_seconds,
        }

    @app.get("/api/v1/sessions", response_model=list[SessionRow])
    async def list_sessions():
        return [
            {"id": s.id, "title": s.title, "status": s.status, "accepted_count": len(s.accepted)}
            for s in manager().sessions.values()
        ]

    @app.post("/api/v1/sessions", status_code=201, response_model=SessionSnapshot)
    async def create_session(body: CreateSession):
        return manager().create(body.title, body.meeting_date).snapshot()

    @app.get("/api/v1/sessions/{session_id}", response_model=SessionSnapshot)
    async def get_session(session_id: str):
        return manager().get(session_id).snapshot()

    @app.post("/api/v1/sessions/{session_id}/chunks", status_code=202, response_model=ChunkReceipt)
    async def append_chunk(session_id: str, body: ChunkInput):
        return await manager().append(session_id, body)

    @app.post(
        "/api/v1/sessions/{session_id}/finalize", status_code=202, response_model=FinalizeReceipt
    )
    async def finalize(session_id: str):
        session = await manager().finalize(session_id)
        return {"id": session.id, "status": session.status}

    @app.delete("/api/v1/sessions/{session_id}", status_code=204)
    async def delete(session_id: str):
        await manager().delete(session_id)

    @app.get("/api/v1/sessions/{session_id}/events")
    async def events(
        session_id: str, request: Request, last_event_id: str | None = Header(default=None)
    ):
        session = manager().get(session_id)
        try:
            initial_cursor = int(last_event_id) if last_event_id else None
            if initial_cursor is not None and initial_cursor < 0:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(400, "invalid_event_cursor") from exc

        async def generate():
            cursor = initial_cursor
            while not await request.is_disconnected():
                changed = session.events.changed
                batch = session.events.after(cursor) if cursor is not None else None
                if batch is None:
                    cursor = session.events.sequence
                    yield Event(cursor, "session.snapshot", session.snapshot()).wire()
                else:
                    for event in batch:
                        cursor = event.id
                        yield event.wire()
                if session.status in TERMINAL and cursor == session.events.sequence:
                    yield Event(cursor, "session.snapshot", session.snapshot()).wire()
                    return
                try:
                    await asyncio.wait_for(changed.wait(), 15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/samples", response_model=list[SampleInfo])
    async def samples():
        return [
            {
                "id": t.id,
                "meeting_id": t.meeting_id,
                "variant": t.variant,
                "title": t.title,
                "segments": len(t.segments),
                "characters": sum(len(s.text) for s in t.segments),
            }
            for t in read_transcripts(settings.corpus_dir)
        ]

    @app.get("/api/v1/samples/{sample_id}", response_model=Transcript)
    async def sample(sample_id: str):
        for transcript in read_transcripts(settings.corpus_dir):
            if transcript.id == sample_id:
                return transcript.model_dump()
        raise HTTPException(404, "sample_not_found")

    @app.api_route("/api", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/api/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def missing_api(path: str = ""):
        raise HTTPException(404, "API route not found")

    if frontend:
        if not (dist / "index.html").is_file():
            raise RuntimeError("前端构建缺失，请先执行 pnpm --dir src/web run build")
        app.frontend("/", directory=dist, fallback="index.html", check_dir=True)
    return app
