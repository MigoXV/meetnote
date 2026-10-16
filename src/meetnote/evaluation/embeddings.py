"""向量接入对照：原文、事实、产物及事件只驻留内存，终端输出统计。"""

import json
from time import perf_counter

from meetnote.corpus.reader import read_transcripts
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.models import create_model
from meetnote.engine.runtime import MeetingEngine
from meetnote.evaluation.paired import ExtractionReplay


async def compare_embeddings(settings, sample_id, directory, emit=print, fulltext=True):
    if settings.provider != "responses":
        raise ValueError("向量对照要求真实主模型")
    sample = next(s for s in read_transcripts(directory) if s.id == sample_id)
    records, summaries = {}, []
    text = "\n".join((s.speaker or "") + "：" + s.text for s in sample.segments)
    for enabled in (False, True):
        config = settings.model_copy(update={"embedding_enabled": enabled})
        model = ExtractionReplay(create_model(config), records)
        recalled_pairs = 0
        last_progress = perf_counter()

        def observe(kind, data):
            nonlocal recalled_pairs, last_progress
            if kind == "embedding.recalled":
                recalled_pairs += data["pairs"]
            if perf_counter() - last_progress > 30:
                emit(
                    json.dumps(
                        {
                            "record": "embedding_progress",
                            "enabled": enabled,
                            "stage": kind,
                            "consumed": engine.stream.consumed,
                            "accepted": engine.stream.cursor,
                            "requests": ctx.stats["requests"],
                        },
                        ensure_ascii=False,
                    )
                )
                last_progress = perf_counter()

        ctx = Context(config, model, observe)
        engine = MeetingEngine(ctx, sample.title, sample.meeting_date)
        started = perf_counter()
        error = None
        try:

            async def chunks():
                offset = 0
                for i, segment in enumerate(sample.segments, 1):
                    value = segment.text + "\n"
                    yield TranscriptChunk(
                        chunk_id=segment.id,
                        speaker=segment.speaker,
                        text=value,
                        seq=i,
                        start=offset,
                        end=offset + len(value),
                    )
                    offset += len(value)

            await engine.run(chunks())
            minutes_seconds = perf_counter() - started
            outputs = (
                await engine.generate_fulltext(sample_id, text, ["abstract", "actions"])
                if fulltext
                else {}
            )
            valid_refs = all(
                set(f.source_ids) <= engine.source_index.keys() for f in engine.knowledge.facts
            )
            failed_products = bool(outputs.get("message"))
            if failed_products:
                error = "fulltext_product_failed"
            summary = {
                "record": "embedding_comparison",
                "sample": sample_id,
                "enabled": enabled,
                "error": error,
                "input_chars": len(text),
                "segments": len(sample.segments),
                "minutes_seconds": round(minutes_seconds, 3),
                "total_seconds": round(perf_counter() - started, 3),
                "remaining_chars": engine.stream.cursor - engine.stream.consumed,
                "processed_sources": len(engine.stream.dispositions),
                "sources": len(engine.stream.sources),
                "valid_fact_references": valid_refs,
                "facts": len(engine.knowledge.facts),
                "active_facts": len(engine.knowledge.summaries),
                "semantic_pairs": recalled_pairs,
                "stats": ctx.stats,
                "extract_replay_hits": model.hits,
                "extract_replay_misses": model.misses,
                "abstract_chars": len(outputs.get("final_abstract", "")),
                "actions_chars": len(outputs.get("next_actions", {}).get("markdown", "")),
            }
            # 参考标注仅在模型处理完后读取，结果只报告证据覆盖，不冒充语义准确率。
            annotation_file = directory / "annotations.jsonl"
            if annotation_file.is_file():
                annotation = next(
                    json.loads(line)
                    for line in annotation_file.read_text().splitlines()
                    if json.loads(line)["transcript_id"] == sample_id
                )
                golds = [
                    g
                    for key in ("facts", "decisions", "actions", "open_questions")
                    for g in annotation.get(key, [])
                ]
                represented = {
                    engine.source_index[sid].chunk_id
                    for f in engine.knowledge.facts
                    for sid in f.source_ids
                }
                summary["gold_source_coverage"] = {
                    "covered": sum(bool(represented & set(g["segment_ids"])) for g in golds),
                    "total": len(golds),
                }
        except Exception as exc:
            summary = {
                "record": "embedding_comparison",
                "sample": sample_id,
                "enabled": enabled,
                "error": type(exc).__name__ + ":" + str(exc),
                "elapsed_seconds": round(perf_counter() - started, 3),
                "stats": ctx.stats,
            }
        finally:
            await model.close()
        summaries.append(summary)
        emit(json.dumps(summary, ensure_ascii=False))
    return summaries
