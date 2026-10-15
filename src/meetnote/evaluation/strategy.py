"""同一模型后端的调用策略对照；所有内容与结果仅在内存中。"""

import json
from statistics import median

from meetnote.engine.models import model_info
from meetnote.evaluation.formal import run_formal


async def compare_strategy(settings, directory, case, emit=print, rounds=3):
    info = await model_info(settings)
    emit(json.dumps({"record": "strategy_model", "info": info}, ensure_ascii=False))
    timings = {"baseline": [], "optimized": []}
    failures = 0
    for repetition in range(rounds):
        order = ("baseline", "optimized") if repetition % 2 == 0 else ("optimized", "baseline")
        for variant in order:
            optimized = variant == "optimized"
            config = settings.model_copy(
                update={
                    "extraction_window": 2 if optimized else 1,
                    "local_extraction_retry": optimized,
                    "parallel_chapters": optimized,
                }
            )

            def report(line):
                row = json.loads(line)
                row.update(variant=variant, repetition=repetition + 1)
                if row["record"] == "result" and not row["error"] and row["remaining"] == 0:
                    timings[variant].append(row["elapsed_seconds"])
                emit(json.dumps(row, ensure_ascii=False))

            failures += await run_formal(config, directory, [case], report, stream_input=optimized)
    complete = all(len(values) == rounds for values in timings.values())
    summary = {
        "record": "strategy_comparison",
        "timings": timings,
        "complete": complete,
        "failures": failures,
    }
    if complete:
        before, after = (median(timings[k]) for k in ("baseline", "optimized"))
        summary.update(
            baseline_median=before,
            optimized_median=after,
            reduction_percent=100 * (before - after) / before,
            speedup=before / after,
        )
    emit(json.dumps(summary, ensure_ascii=False))
    return summary
