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
from meetnote.engine.models import create_model
from meetnote.sessions.manager import SessionError, SessionManager

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
        model = create_model(settings)
        manager = SessionManager(settings, model)
        try:
            session = manager.create(sample.title, sample.meeting_date)
            for segment in sample.segments:
                while True:
                    try:
                        await manager.append(
                            session.id,
                            ChunkInput(
                                chunk_id=segment.id, text=segment.text, speaker=segment.speaker
                            ),
                        )
                        break
                    except SessionError as exc:
                        if exc.code != "input_queue_full":
                            raise
                        await asyncio.sleep(0.05)
            await manager.finalize(session.id)
            await session.task
            typer.echo(json.dumps(session.snapshot(), ensure_ascii=False, indent=2))
            if session.status != "completed":
                raise typer.Exit(1)
        finally:
            await manager.close()

    asyncio.run(run())


@app.command("check-short")
def check_short(
    case: Annotated[list[str] | None, typer.Option(help="只运行指定短案例，可重复指定")] = None,
):
    """显式调用真实 Responses 服务，结果仅输出到标准输出。"""
    from meetnote.evaluation.short import CASES, run_short

    names = case or list(CASES)
    if any(name not in CASES for name in names):
        raise typer.BadParameter("未知短案例，可用：" + ", ".join(CASES))
    if asyncio.run(run_short(Settings(), names, typer.echo)):
        raise typer.Exit(1)


@app.command("compare-strategy")
def compare_strategy_command(
    case: Annotated[str, typer.Option()] = "business_release_10k",
    directory: Annotated[Path, typer.Option()] = Path("data-bin/data"),
    rounds: Annotated[int, typer.Option(min=1, max=10)] = 3,
):
    """同一主模型，交替运行串行基线与优化策略，不复用抽取结果。"""
    from meetnote.evaluation.strategy import compare_strategy

    samples = {s.id for s in read_transcripts(directory)}
    if case not in samples:
        raise typer.BadParameter("正式语料 ID 不存在")
    result = asyncio.run(compare_strategy(Settings(), directory, case, typer.echo, rounds))
    if not result["complete"]:
        raise typer.Exit(1)


@app.command("evaluate-formal")
def evaluate_formal(
    case: Annotated[list[str], typer.Option(help="正式语料 ID，可重复指定")],
    directory: Annotated[Path, typer.Option()] = Path("data-bin/data"),
):
    """最后阶段的长会议验收。须先通过离线测试与 check-short。"""
    from meetnote.evaluation.formal import run_formal

    samples = {s.id for s in read_transcripts(directory)}
    if any(name not in samples for name in case):
        raise typer.BadParameter("正式语料 ID 不存在")
    if asyncio.run(run_formal(Settings(), directory, case, typer.echo)):
        raise typer.Exit(1)


@app.command("check-fragment")
def check_fragment(
    case: Annotated[str, typer.Option()],
    segments: Annotated[int, typer.Option(min=1, max=20)] = 12,
    start_segment: Annotated[int, typer.Option(min=1, help="从第几条发言开始（从 1 计数）")] = 1,
    directory: Annotated[Path, typer.Option()] = Path("data-bin/data"),
):
    """只跑长会的局部发言，用于复现批量错误；不计为整场验收。"""
    from meetnote.evaluation.formal import run_formal

    samples = {s.id: s for s in read_transcripts(directory)}
    if case not in samples or start_segment > len(samples[case].segments):
        raise typer.BadParameter("样本或起始发言不存在")
    if asyncio.run(run_formal(Settings(), directory, [case], typer.echo, segments, start_segment)):
        raise typer.Exit(1)


@app.command("evaluate-decisions")
def evaluate_decisions(
    simulate: bool = typer.Option(False, help="只运行明确标记的标准答案与错误注入模拟"),
):
    """主模型字段及关系核验；标注与真实模型输入分离，结果只输出 stdout。"""
    from meetnote.evaluation.decisions import run_decisions, run_simulated_decisions

    if simulate:
        from meetnote.evaluation.sensitivity import run_sensitivity

        run_simulated_decisions(typer.echo)
        asyncio.run(run_sensitivity(typer.echo))
    else:
        asyncio.run(run_decisions(Settings(), typer.echo))


@app.command("check-integration")
def check_integration(
    url: str = typer.Option("http://127.0.0.1:8012", help="真实主模型 MeetNote 实例"),
):
    """短流程通过后回放完整会议；仅终端报告，不写会议文件。"""
    from meetnote.evaluation.integration import run_integration

    if asyncio.run(run_integration(Settings(), url, typer.echo)):
        raise typer.Exit(1)


@app.command("review-quality")
def review_quality(
    sample: str = typer.Option("product_clean", help="完整会议转写 ID"),
    details: bool = typer.Option(False, help="在终端显示实际纪要及事实；不落盘"),
):
    """复核主模型的完整会议产出，标注只用于事后复核。"""
    from meetnote.evaluation.quality import run_quality

    asyncio.run(run_quality(Settings(), sample, details, typer.echo))


if __name__ == "__main__":
    app()
