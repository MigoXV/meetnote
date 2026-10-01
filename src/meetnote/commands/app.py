import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

import typer
import uvicorn

from meetnote.config import Settings
from meetnote.corpus.reader import read_transcripts, validate_corpus
from meetnote.domain.transcripts import ChunkInput
from meetnote.llm.client import ResponsesClient
from meetnote.llm.mock import MockClient
from meetnote.sessions.manager import SessionManager

app = typer.Typer(no_args_is_help=True, help="MeetNote：会议摘要 sidecar")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


@app.command()
def serve(
    host: Annotated[str, typer.Option(envvar="MEETNOTE_HOST")] = "127.0.0.1",
    port: Annotated[int, typer.Option(envvar="MEETNOTE_PORT")] = 8010,
    mock: Annotated[bool, typer.Option(help="使用模拟模型验证接线")] = False,
):
    """启动 API 和统一托管的调试页面。"""
    from meetnote.web.factory import create_app

    settings = Settings()
    if mock:
        settings.provider = "mock"
    uvicorn.run(create_app(settings), host=host, port=port)


@app.command("validate-corpus")
def validate(
    directory: Annotated[Path, typer.Option(envvar="MEETNOTE_CORPUS_DIR")] = Path(
        "/workspace/model-bin/meetnote"
    ),
):
    """校验语料配对、标注引用及识别错误跨度。"""
    typer.echo(json.dumps(validate_corpus(directory), ensure_ascii=False, indent=2))


@app.command()
def replay(
    sample_id: Annotated[str, typer.Argument(help="语料中的转写 ID")],
    directory: Annotated[Path, typer.Option(envvar="MEETNOTE_CORPUS_DIR")] = Path(
        "/workspace/model-bin/meetnote"
    ),
    mock: Annotated[bool, typer.Option()] = False,
):
    """通过同一个会话引擎回放，最终结果输出到标准输出。"""
    samples = {s.id: s for s in read_transcripts(directory)}
    if sample_id not in samples:
        raise typer.BadParameter("样本不存在")
    sample = samples[sample_id]
    settings = Settings()
    if mock:
        settings.provider = "mock"

    async def run():
        model = MockClient() if settings.provider == "mock" else ResponsesClient(settings)
        manager = SessionManager(settings, model)
        try:
            session = manager.create(sample.title)
            for segment in sample.segments:
                await manager.append(
                    session.id,
                    ChunkInput(chunk_id=segment.id, text=segment.text, speaker=segment.speaker),
                )
            await manager.finalize(session.id)
            await session.task
            typer.echo(json.dumps(session.snapshot(), ensure_ascii=False, indent=2))
            if session.status != "completed":
                raise typer.Exit(1)
        finally:
            await manager.close()

    asyncio.run(run())


if __name__ == "__main__":
    app()
