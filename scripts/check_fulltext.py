"""通过真实 HTTP 全流程回放；仅输出统计，不保存原文、事件或产物。"""

import asyncio
import json
from pathlib import Path
from time import perf_counter

import grpc
import httpx
import typer

from meetnote.config import Settings
from meetnote.corpus.reader import read_transcripts
from meetnote.domain.fulltext import DEFAULT_PIPELINE
from meetnote.embeddings.proto import embedding_pb2 as pb
from meetnote.embeddings.proto import embedding_pb2_grpc as rpc

app = typer.Typer()


@app.command()
def main(
    sample: str = typer.Option("business_release_10k"),
    directory: Path = typer.Option(Path("data-bin/data"), envvar="MEETNOTE_CORPUS_DIR"),
    url: str = typer.Option("http://127.0.0.1:8013", envvar="MEETNOTE_SERVICE_URL"),
):
    asyncio.run(run(sample, directory, url))


async def metadata():
    async with grpc.aio.insecure_channel(Settings().embedding_target) as channel:
        result = await rpc.EmbeddingServiceStub(channel).Metadata(pb.MetadataRequest(), timeout=5)
        assert result.ready
        return result


async def run(sample_id, directory, url):
    sample = next(s for s in read_transcripts(directory) if s.id == sample_id)
    text = "\n".join((s.speaker or "") + "：" + s.text for s in sample.segments)
    before = await metadata()
    started = perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(1800, connect=10)) as client:

        async def request():
            response = await client.post(
                url.rstrip("/") + "/v1/fulltext/generate",
                json={
                    "id": "embedding-check-" + sample_id,
                    "text": text,
                    "pipeline": list(DEFAULT_PIPELINE),
                },
            )
            response.raise_for_status()
            body = response.json()
            after = await metadata()
            assert after.generation == before.generation
            summary = {
                "record": "http_fulltext",
                "sample": sample_id,
                "chars": len(text),
                "segments": len(sample.segments),
                "elapsed_s": round(perf_counter() - started, 3),
                "status": response.status_code,
                "fields": list(body),
                "message": body.get("message"),
                "abstract_chars": len(body.get("final_abstract", "")),
                "minutes_chars": len(body.get("minutes", {}).get("markdown", "")),
                "actions_chars": len(body.get("next_actions", {}).get("markdown", "")),
                "embedding": {
                    key: getattr(after, key) - getattr(before, key)
                    for key in (
                        "requests",
                        "completed",
                        "batches",
                        "texts",
                        "failed",
                        "cancelled",
                        "expired",
                    )
                },
            }
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            assert not body.get("message"), body.get("message")
            assert all(
                summary[name] > 0 for name in ("abstract_chars", "minutes_chars", "actions_chars")
            )

        task = asyncio.create_task(request())
        while not task.done():
            await asyncio.wait({task}, timeout=30)
            if not task.done():
                print(
                    json.dumps(
                        {
                            "record": "http_fulltext_progress",
                            "elapsed_s": round(perf_counter() - started),
                            "input_chars": len(text),
                        }
                    ),
                    flush=True,
                )
        await task


if __name__ == "__main__":
    app()
