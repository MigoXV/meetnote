"""完整会议效果复核：同输入、内存抽取复用，标注在推理结束后读取。"""

import asyncio
import json

from meetnote.corpus.reader import read_transcripts
from meetnote.domain.transcripts import ChunkInput
from meetnote.engine.models import create_model
from meetnote.evaluation.paired import ExtractionReplay, comparison_info
from meetnote.sessions.manager import SessionManager


async def run_quality(settings, sample_id="product_clean", details=False, emit=print):
    info = await comparison_info(settings)
    emit(json.dumps({"record": "comparison_model", "model": info}, ensure_ascii=False))
    sample = next(s for s in read_transcripts(settings.corpus_dir) if s.id == sample_id)
    records = {}
    results = {}
    # 先检查用户选择的 BF16，再使用相同抽取请求复核旧方案。
    for backend in ("laya", "responses"):
        config = settings.model_copy(update={"decision_backend": backend, "laya_fallback": False})
        model = ExtractionReplay(create_model(config), records)
        manager = SessionManager(config, model)
        try:
            session = manager.create(sample.title, sample.meeting_date)
            for segment in sample.segments:
                await manager.append(
                    session.id,
                    ChunkInput(
                        chunk_id=segment.id,
                        text=segment.text,
                        speaker=segment.speaker,
                    ),
                )
            await manager.finalize(session.id)
            while not session.task.done():
                await asyncio.wait({session.task}, timeout=30)
                if not session.task.done():
                    emit(
                        json.dumps(
                            {
                                "record": "quality_progress",
                                "backend": backend,
                                "processed": session.engine.stream.processed,
                                "remaining": session.chars - session.engine.stream.consumed,
                            },
                            ensure_ascii=False,
                        )
                    )
            await session.task
            snapshot = session.snapshot()
            results[backend] = snapshot
            emit(
                json.dumps(
                    {
                        "record": "quality_summary",
                        "backend": backend,
                        "sample": sample_id,
                        "status": snapshot["status"],
                        "error": snapshot["error"],
                        "warnings": snapshot["warnings"],
                        "remaining_chars": snapshot["remaining_chars"],
                        "facts": len(snapshot["facts"]),
                        "stats": snapshot["stats"],
                        "extract_replay_hits": model.hits,
                        "extract_new_requests": model.misses,
                    },
                    ensure_ascii=False,
                )
            )
            if details:
                emit(
                    json.dumps(
                        {
                            "record": "quality_output",
                            "backend": backend,
                            "facts": snapshot["facts"],
                            "minutes": snapshot["minutes"],
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            await manager.close()
    # 标注与模型 Context 完全隔离，不用模型给自己评分。
    annotation = next(
        json.loads(line)
        for line in (settings.corpus_dir / "annotations.jsonl").read_text().splitlines()
        if json.loads(line)["transcript_id"] == sample_id
    )
    for category in ("facts", "decisions", "actions", "open_questions"):
        for index, gold in enumerate(annotation.get(category, [])):
            review = {
                "record": "quality_review",
                "category": category,
                "check": index + 1,
                "gold": gold,
            }
            for backend, snapshot in results.items():
                segments = {s["id"]: s["chunk_id"] for s in snapshot["sources"]}
                review[backend] = [
                    {k: f[k] for k in ["text", "owner", "due", "kind", "status", "source_ids"]}
                    for f in snapshot["facts"]
                    if any(segments[s] in gold["segment_ids"] for s in f["source_ids"])
                ]
            emit(json.dumps(review, ensure_ascii=False))
    return results
