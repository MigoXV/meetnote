"""固定合成抽取产物，真实执行 MeetingEngine；不调用生成模型。"""

import json
from collections import Counter

from meetnote.config import Settings
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.evaluation.decision_cases import CASES
from meetnote.evaluation.decisions import oracle, perturb
from meetnote.llm.client import ModelFailure
from meetnote.llm.mock import MockClient, mock_result


class FrozenExtraction(MockClient):
    """仅用于显式模拟。输入事实固定；gold 不发送给任何真实后端。"""

    def __init__(self, case, rate=0, seed=0, profile="mixed"):
        self.case, self.rate, self.seed, self.profile = case, rate, seed, profile
        self.asked = 0
        self.injected = 0
        self.uncovered = 0
        self.blocked = 0

    async def generate(self, task, instructions, context, schema):
        case = self.case
        if task == "extract":
            facts = []
            for source in context["sources"]:
                old = case.task == "reconcile" and source["text"] == case.old
                status = (
                    case.old_status
                    if old
                    else case.new_status
                    if case.task == "reconcile"
                    else "confirmed"
                )
                kind = (
                    "proposal"
                    if status == "proposed"
                    else "question"
                    if old and status == "unresolved"
                    else "action"
                    if case.task == "fields"
                    else "fact"
                )
                fact = {
                    "text": case.old if old else case.claim,
                    "kind": kind,
                    "status": status,
                    "topic": "合成决策实验",
                    "subject": "本案例事项",
                    "source_ids": [source["id"]],
                }
                if case.task == "fields":
                    fact[case.field] = case.value
                    fact[case.field + "_source_ids"] = [source["id"]]
                facts.append(fact)
            return mock_result(task, context, schema, {"facts": facts})
        if task not in {"fields", "reconcile"}:
            return await super().generate(task, instructions, context, schema)
        if task != case.task:
            self.uncovered += 1
            raise ModelFailure("SIMULATED_unlabelled_task")
        expected, changed = perturb(case, oracle(case), self.rate, self.seed, self.profile)
        output = {}
        if task == "fields":
            for claim in context["claims"]:
                if claim["text"] != case.claim:
                    self.uncovered += 1
                    raise ModelFailure("SIMULATED_unlabelled_claim")
                output[claim["id"]] = expected["f1"]
                self.asked += 1
                self.injected += changed
        else:
            for key, pair in context["pairs"].items():
                if pair["new_claim"] != case.claim or pair["old_claim"] != case.old:
                    self.uncovered += 1
                    raise ModelFailure("SIMULATED_unlabelled_pair")
                value = dict(expected["p0"])
                # 证据索引来自当前请求，不能补造未召回的引用。
                value["evidence_id"] = None if value["kind"] == "none" else pair["evidence_ids"][0]
                output[key] = value
                self.asked += 1
                self.injected += changed
        try:
            return schema.model_validate(output)
        except ValueError as exc:
            self.blocked += 1
            raise ModelFailure("SIMULATED_decision_blocked_by_contract") from exc


async def scenario(case, rate=0, seed=0, profile="mixed"):
    model = FrozenExtraction(case, rate, seed, profile)
    settings = Settings(_env_file=None, provider="mock", checkpoint_seconds=999)
    engine = MeetingEngine(Context(settings, model, lambda *_: None), "SIMULATED 决策实验")
    error = None
    try:
        texts = [case.text] if case.task == "fields" else [case.old, case.text]
        for index, text in enumerate(texts):
            start = engine.stream.cursor
            await engine.append(
                TranscriptChunk(
                    chunk_id=f"c{index}",
                    seq=index + 1,
                    text=text,
                    start=start,
                    end=start + len(text),
                    speaker="主持人",
                )
            )
            await engine.cycle("flush")
        await engine.finalize()
    except Exception as exc:
        error = str(exc)
    facts = engine.knowledge.facts
    if case.task == "fields":
        expected = case.value if case.expected else ""
        correct = bool(facts) and getattr(facts[-1], case.field) == expected
    else:
        old = next((f for f in facts if f.text == case.old), None)
        target = {
            "supersedes": "superseded",
            "retracts": "retracted",
            "resolves": "resolved",
            "conflicts": "conflicted",
            "none": case.old_status,
        }[case.expected]
        correct = old is not None and old.status == target
    residual_claim = (
        case.task == "fields"
        and not case.expected
        and case.value in case.claim
        and any(f.text == case.claim for f in facts)
    )
    return {
        "case": case.id,
        "passed": correct and not error and not residual_claim,
        "ledger_passed": correct and not error,
        "residual_unsupported_claim": residual_claim,
        "error": error,
        "asked": model.asked,
        "injected": model.injected,
        "uncovered": model.uncovered,
        "blocked": model.blocked,
        "processed": engine.stream.consumed == engine.stream.cursor,
        "facts": len(facts),
    }


async def run_sensitivity(emit=print, seeds=range(5)):
    # baseline 每个 case 只执行一次，后续分档仅做已定义的受控干预。
    baseline = [await scenario(case) for case in CASES]
    emit(
        json.dumps(
            {
                "record": "SIMULATED_pipeline_oracle",
                "cases": baseline,
                "passed": sum(x["passed"] for x in baseline),
                "ledger_passed": sum(x["ledger_passed"] for x in baseline),
                "measurement": "字段/旧事实状态与明确错误字段仍残留于正文；不是完整纪要语义评分",
                "total": len(baseline),
            },
            ensure_ascii=False,
        )
    )
    for profile in ("mixed", "field_delete", "field_accept", "relation_miss", "relation_false"):
        for rate in (0.01, 0.05, 0.1, 0.2):
            counts = Counter()
            failures = Counter()
            for seed in seeds:
                for case in CASES:
                    row = await scenario(case, rate, seed, profile)
                    counts["total"] += 1
                    counts["passed"] += row["passed"]
                    counts["ledger_passed"] += row["ledger_passed"]
                    counts["residual_unsupported_claim"] += row["residual_unsupported_claim"]
                    for key in ("asked", "injected", "uncovered", "blocked"):
                        counts[key] += row[key]
                    if not row["passed"]:
                        failures[row["case"]] += 1
            emit(
                json.dumps(
                    {
                        "record": "SIMULATED_pipeline_noise",
                        "profile": profile,
                        "requested_error_rate": rate,
                        "actual_decision_error_rate": counts["injected"] / max(1, counts["asked"]),
                        "counts": dict(counts),
                        "failed_cases": dict(failures),
                        "seeds": list(seeds),
                    },
                    ensure_ascii=False,
                )
            )
