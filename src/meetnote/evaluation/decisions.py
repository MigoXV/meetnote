"""真实决策基线与显式 oracle/noise 模拟。所有运行产物仅驻内存。"""

import hashlib
import json
import random
from collections import Counter
from time import perf_counter

from meetnote.engine.context import Context
from meetnote.engine.models import create_model
from meetnote.evaluation.decision_cases import CASES


def oracle(case):
    if case.task == "fields":
        row = {"owner_supported": True, "due_supported": True, "reason": "SIMULATED: 人工合成标注"}
        row[case.field + "_supported"] = case.expected
        return {"f1": row}
    return {
        "p0": {
            "kind": case.expected,
            "evidence_id": None if case.expected == "none" else "s2.0#0",
            "reason": "SIMULATED: 人工合成标注",
        }
    }


def grade(case, result):
    if case.task == "fields":
        return result["f1"][case.field + "_supported"]
    return result["p0"]["kind"]


def perturb(case, result, rate, seed, profile="mixed"):
    """只修改已标注决策。每个案例独立播种，与遍历顺序无关。"""
    result = json.loads(json.dumps(result))
    rng = random.Random(hashlib.sha256(f"{seed}:{case.id}:{profile}".encode()).digest())
    target = (
        "field_delete"
        if case.task == "fields" and case.expected
        else "field_accept"
        if case.task == "fields"
        else "relation_miss"
        if case.expected != "none"
        else "relation_false"
    )
    if profile not in {"mixed", target} or rng.random() >= rate:
        return result, False
    if case.task == "fields":
        result["f1"][case.field + "_supported"] = not case.expected
    else:
        kind = "none" if case.expected != "none" else "supersedes"
        result["p0"].update(kind=kind, evidence_id=None if kind == "none" else "s2.0#0")
    return result, True


async def run_decisions(settings, emit=print):
    if settings.provider != "responses":
        raise ValueError("真实对照不能使用 mock 主模型")
    summaries = []
    for backend in ("responses",):
        model = create_model(settings)
        counts = Counter()
        started = perf_counter()
        ctx = Context(settings, model, lambda *_: None)
        try:
            for case in CASES:
                payload, schema = case.payload()
                error = None
                try:
                    result = await ctx.invoke(case.task, payload, schema)
                    prediction = grade(case, result.model_dump())
                except Exception as exc:
                    prediction = "ERROR"
                    error = str(exc)
                correct = prediction == case.expected
                counts["errors"] += error is not None
                group = case.field or "relation"
                counts[group + "_total"] += 1
                counts[group + "_correct"] += correct
                counts["correct"] += correct
                counts["total"] += 1
                counts[case.task + "_correct"] += correct
                counts[case.task + "_total"] += 1
                counts[f"confusion:{case.expected}->{prediction}"] += 1
                emit(
                    json.dumps(
                        {
                            "record": "decision",
                            "backend": backend,
                            "case": case.id,
                            "expected": case.expected,
                            "prediction": prediction,
                            "correct": correct,
                            "error": error,
                        },
                        ensure_ascii=False,
                    )
                )
        finally:
            await model.close()
        summary = {
            "record": "decision_summary",
            "backend": backend,
            "counts": dict(counts),
            "elapsed_seconds": round(perf_counter() - started, 2),
            "stats": ctx.stats,
        }
        summaries.append(summary)
        emit(json.dumps(summary, ensure_ascii=False))
    return summaries


def run_simulated_decisions(emit=print):
    for profile in ("mixed", "field_delete", "field_accept", "relation_miss", "relation_false"):
        for rate in (0, 0.01, 0.05, 0.1, 0.2):
            errors = total = 0
            confusion = Counter()
            for seed in range(10):
                for case in CASES:
                    result, changed = perturb(case, oracle(case), rate, seed, profile)
                    prediction = grade(case, result)
                    total += 1
                    errors += changed
                    confusion[f"{case.expected}->{prediction}"] += 1
            emit(
                json.dumps(
                    {
                        "record": "SIMULATED_decisions",
                        "profile": profile,
                        "requested_error_rate": rate,
                        "actual_error_rate": errors / total,
                        "total": total,
                        "errors": errors,
                        "seeds": list(range(10)),
                        "confusion": dict(confusion),
                    },
                    ensure_ascii=False,
                )
            )
