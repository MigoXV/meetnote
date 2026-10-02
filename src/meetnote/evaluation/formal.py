"""真实长会验收：只读输入，结果与对照仅输出 stdout，不生成会议文件。"""

import json
from pathlib import Path
from time import perf_counter

from meetnote.corpus.reader import read_transcripts
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ResponsesClient


async def run_formal(
    settings, directory: Path, names, emit=print, limit_segments=0, start_segment=1
):
    samples = {s.id: s for s in read_transcripts(directory)}
    manifest = {
        s["case_id"]: s for s in json.loads((directory / "dataset_manifest.json").read_text())
    }
    model = ResponsesClient(settings)
    failures = 0
    try:
        for name in names:
            sample = samples[name]
            segments = (
                sample.segments[start_segment - 1 : start_segment - 1 + limit_segments]
                if limit_segments
                else sample.segments
            )
            started = perf_counter()
            last_progress = started

            def observe(kind, data):
                nonlocal last_progress
                if perf_counter() - last_progress > 30:
                    emit(
                        json.dumps(
                            {
                                "record": "progress",
                                "case": name,
                                "stage": kind,
                                "stats": context.stats,
                                "consumed": engine.stream.consumed,
                                "accepted": engine.stream.cursor,
                            },
                            ensure_ascii=False,
                        )
                    )
                    last_progress = perf_counter()
                if kind in {"model.validation_failed", "fields.corrected", "node.degraded"} or (
                    kind == "model.request" and data.get("error")
                ):
                    emit(
                        json.dumps(
                            {"record": "diagnostic", "case": name, "kind": kind, "data": data},
                            ensure_ascii=False,
                        )
                    )

            context = Context(settings, model, observe)
            engine = MeetingEngine(context, sample.title, sample.meeting_date)
            error = None
            try:
                for i, segment in enumerate(segments):
                    start = engine.stream.cursor
                    await engine.append(
                        TranscriptChunk(
                            chunk_id=segment.id,
                            speaker=segment.speaker,
                            text=segment.text + "\n",
                            seq=i + 1,
                            start=start,
                            end=start + len(segment.text) + 1,
                        )
                    )
                await engine.finalize()
            except Exception as exc:
                error = str(exc)
                failures += 1
            emit(
                json.dumps(
                    {
                        "record": "result",
                        "case": name,
                        "error": error,
                        "elapsed_seconds": round(perf_counter() - started, 2),
                        "stats": context.stats,
                        "processed_sources": len(engine.stream.dispositions),
                        "sources": len(engine.stream.sources),
                        "facts": len(engine.knowledge.facts),
                        "warnings": engine.knowledge.warnings,
                        "accepted_segments": engine.stream.processed,
                        "expected_segments": len(segments),
                        "scope": "fragment" if limit_segments else "full",
                        "first_chunk": segments[0].id if segments else None,
                        "last_chunk": segments[-1].id if segments else None,
                        "remaining": engine.stream.cursor - engine.stream.consumed,
                    },
                    ensure_ascii=False,
                )
            )
            # gold 在模型处理完成后才读取，且永不写回任务 Context。
            gold_path = (directory / manifest[name]["file"]).with_suffix(".gold.json")
            gold = json.loads(gold_path.read_text())
            for check in gold["checks"]:
                sources = set(check["source_ids"])
                if limit_segments and not sources <= {s.id for s in segments}:
                    continue
                matched = []
                for fact in engine.knowledge.facts:
                    if any(engine.source_index[s].chunk_id in sources for s in fact.source_ids):
                        matched.append(
                            {
                                "id": fact.id,
                                "text": fact.text,
                                "owner": fact.owner,
                                "due": fact.due,
                                "kind": fact.kind,
                                "status": fact.status,
                                "related_ids": fact.related_ids,
                            }
                        )
                emit(
                    json.dumps(
                        {"record": "gold_review", "case": name, "check": check, "facts": matched},
                        ensure_ascii=False,
                    )
                )
            for revision in engine.knowledge.revisions:
                emit(
                    json.dumps(
                        {
                            "record": "revision_review",
                            "case": name,
                            "before": revision.before.model_dump(),
                            "after": revision.after.model_dump(),
                            "reason": revision.reason,
                            "related_facts": [
                                f.model_dump()
                                for f in engine.knowledge.facts
                                if f.id in revision.after.related_ids
                            ],
                        },
                        ensure_ascii=False,
                    )
                )
            if engine.knowledge.draft:
                if limit_segments:
                    emit(
                        json.dumps(
                            {
                                "record": "fragment_facts",
                                "case": name,
                                "facts": [f.model_dump() for f in engine.knowledge.facts],
                            },
                            ensure_ascii=False,
                        )
                    )
                for chapter in engine.knowledge.draft.sections:
                    emit(
                        json.dumps(
                            {
                                "record": "chapter_review",
                                "case": name,
                                "chapter": chapter.model_dump(),
                            },
                            ensure_ascii=False,
                        )
                    )
            if error:
                break
    finally:
        await model.close()
    return failures
