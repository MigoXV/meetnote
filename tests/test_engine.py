import asyncio

import pytest

from meetnote.config import Settings
from meetnote.domain.results import FactInput
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ModelFailure
from meetnote.llm.mock import MockClient, mock_result


class Scripted(MockClient):
    def __init__(self, extract=None, reconcile=None, chapter=None, fields=None):
        self.handlers = {
            "extract": extract,
            "reconcile": reconcile,
            "chapter": chapter,
            "fields": fields,
        }
        self.calls = []

    async def generate(self, task, instructions, context, schema):
        self.calls.append((task, context))
        fn = self.handlers[task]
        if fn:
            return mock_result(task, context, schema, fn(context))
        return await super().generate(task, instructions, context, schema)


def engine(model=None, **kwargs):
    events = []
    settings = Settings(_env_file=None, provider="mock", checkpoint_seconds=999, **kwargs)
    return MeetingEngine(
        Context(settings, model or MockClient(), lambda k, d: events.append((k, d)))
    ), events


async def append(runtime, text):
    start = runtime.stream.cursor
    await runtime.append(
        TranscriptChunk(
            chunk_id=f"c{runtime.stream.processed + 1}",
            text=text,
            seq=runtime.stream.processed + 1,
            start=start,
            end=start + len(text),
        )
    )


def fact(source, **kwargs):
    return dict(
        text=source["text"],
        kind="fact",
        status="confirmed",
        topic="预算",
        subject="采购预算",
        source_ids=[source["id"]],
        **kwargs,
    )


async def test_unclosed_discussion_progresses_and_later_conclusion_is_visible():
    model = Scripted()
    runtime, _ = engine(model)
    await append(runtime, "还在讨论，稍后决定。")
    await runtime.cycle("flush")
    before = len(model.calls)
    await runtime.cycle("flush")
    assert len(model.calls) == before
    await append(runtime, "最终确认明天发布。\n")
    await runtime.finalize()
    assert runtime.stream.consumed == runtime.stream.cursor
    assert len(runtime.stream.dispositions) == len(runtime.stream.sources)
    assert "最终确认明天发布" in runtime.knowledge.draft.markdown


async def test_chinese_amount_and_new_question_survive_same_topic():
    def extract(ctx):
        return {"facts": [fact(s) for s in ctx["sources"]]}

    runtime, _ = engine(Scripted(extract=extract))
    for text in ["预算为十万元。", "预算改为二十万元。", "报价是否含税？"]:
        await append(runtime, text)
        await runtime.cycle("flush")
    await runtime.finalize()
    assert len(runtime.knowledge.facts) == 3
    assert "二十万元" in runtime.knowledge.draft.markdown
    assert "含税" in runtime.knowledge.draft.markdown


async def test_revision_retains_history_and_closes_question():
    def extract(ctx):
        output = []
        for s in ctx["sources"]:
            f = fact(s)
            if "？" in s["text"]:
                f.update(kind="question", status="unresolved")
            output.append(f)
        return {"facts": output}

    def reconcile(ctx):
        return {
            "relations": [
                {
                    "new_id": ctx["new"][0]["id"],
                    "old_id": ctx["candidates"][0]["id"],
                    "kind": "resolves",
                    "quote": ctx["new"][0]["text"],
                    "reason": "原文明确答复含税",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "报价是否含税？")
    await runtime.cycle("flush")
    await append(runtime, "确认报价含税。")
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "resolved"
    assert runtime.knowledge.revisions[0].before.status == "unresolved"
    assert "是否含税" not in runtime.knowledge.draft.markdown


async def test_exact_duplicates_merge_evidence_without_reusing_fact_ids():
    runtime, _ = engine(Scripted(extract=lambda c: {"facts": [fact(s) for s in c["sources"]]}))
    for text in ["同一事实。", "同一事实。", "第二事实。", "第三事实。"]:
        await append(runtime, text)
        await runtime.cycle("flush")
    assert len(runtime.knowledge.facts) == 3
    assert len({f.id for f in runtime.knowledge.facts}) == 3
    assert len(runtime.knowledge.facts[0].source_ids) == 2


async def test_duplicate_sequence_cannot_overwrite_source_identity():
    runtime, _ = engine()
    await append(runtime, "第一条。")
    with pytest.raises(ModelFailure, match="input_sequence_invalid"):
        await runtime.append(
            TranscriptChunk(
                chunk_id="other",
                text="第二条。",
                seq=1,
                start=len("第一条。"),
                end=len("第一条。第二条。"),
            )
        )
    assert runtime.source_index["s1.0"].text == "第一条。"
    assert runtime.stream.processed == 1


async def test_repeated_fact_evidence_does_not_expand_relation_request_without_bound():
    def extract(ctx):
        source = ctx["sources"][0]
        value = fact(source)
        if "改为" not in source["text"]:
            value["text"] = "预算原定十万元。"
        return {"facts": [value]}

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    for i in range(30):
        await append(runtime, f"预算原定十万元。第{i}次记录。")
        await runtime.cycle("flush")
    await append(runtime, "预算确认改为二十万元。")
    await runtime.finalize()
    assert len(runtime.knowledge.facts[0].source_ids) == 30
    pages = [payload for task, payload in model.calls if task == "reconcile"]
    assert pages
    assert all(len(f["source_ids"]) <= 2 for p in pages for f in p["candidates"])
    assert any(f["additional_source_count"] == 28 for p in pages for f in p["candidates"])


@pytest.mark.parametrize("mode", ["unknown", "missing", "owner", "failure"])
async def test_invalid_extraction_never_consumes_input(mode):
    def extract(ctx):
        if mode == "failure":
            raise ModelFailure("unavailable")
        f = fact(ctx["sources"][0])
        if mode == "unknown":
            f["source_ids"] = ["unknown"]
        if mode == "owner":
            f["owner"] = "无依据负责人"
        return {"facts": [] if mode == "missing" else [f]}

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, "必须保留的原文。")
    with pytest.raises(ModelFailure):
        await runtime.finalize()
    assert runtime.stream.consumed == 0
    assert not runtime.knowledge.facts
    assert runtime.stream.buffer == "必须保留的原文。"


async def test_whitespace_tail_empty_meeting_and_finalize_idempotence():
    runtime, _ = engine()
    await append(runtime, "确认发布。\n\n")
    await runtime.finalize()
    draft = runtime.knowledge.draft
    await runtime.finalize()
    assert runtime.knowledge.draft is draft
    assert runtime.stream.buffer == ""
    empty, _ = engine()
    await empty.finalize()
    assert empty.knowledge.draft is None


async def test_cancel_discards_candidate_state():
    entered = asyncio.Event()

    class Slow(MockClient):
        async def generate(self, *args):
            entered.set()
            await asyncio.Event().wait()

    runtime, _ = engine(Slow())
    await append(runtime, "尚未成功提交。")
    task = asyncio.create_task(runtime.finalize())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not runtime.knowledge.facts
    assert runtime.stream.consumed == 0


async def test_missing_coverage_splits_batch_without_skipping_sources():
    def extract(ctx):
        return {"facts": [fact(ctx["sources"][0])]}

    runtime, _ = engine(Scripted(extract=extract))
    for text in ["必须记录甲事项。", "必须记录乙事项。", "必须记录丙事项。"]:
        await append(runtime, text)
    await runtime.finalize()
    assert len(runtime.knowledge.facts) == 3
    assert len(runtime.stream.dispositions) == 3
    assert runtime.stream.consumed == runtime.stream.cursor


async def test_unrepaired_field_error_splits_only_affected_batch():
    def extract(ctx):
        facts = [fact(s) for s in ctx["sources"]]
        if len(facts) > 1:
            facts[0].update(kind="action", owner="无依据的负责人")
        return {"facts": facts}

    runtime, _ = engine(Scripted(extract=extract))
    for text in ["应记录甲事项。", "应记录乙事项。"]:
        await append(runtime, text)
    await runtime.finalize()
    assert runtime.stream.consumed == runtime.stream.cursor
    assert len(runtime.knowledge.facts) == 2
    assert all(not f.owner for f in runtime.knowledge.facts)


async def test_wire_category_cannot_create_confirmed_risk():
    class WireModel(MockClient):
        async def generate(self, task, instructions, context, schema):
            if task == "extract":
                source = context["sources"][0]
                return schema.model_validate(
                    {
                        source["id"]: {
                            "facts": [
                                {
                                    "category": "风险",
                                    "text": source["text"],
                                    "topic": "保管风险",
                                    "subject": "保管争议",
                                    "source_ids": [source["id"]],
                                }
                            ],
                            "ignored_reason": "",
                        }
                    }
                )
            return await super().generate(task, instructions, context, schema)

    runtime, _ = engine(WireModel())
    await append(runtime, "提前到货可能发生保管责任争议。")
    await runtime.finalize()
    f = runtime.knowledge.facts[0]
    assert (f.kind, f.status) == ("risk", "unresolved")


async def test_duty_validation_recovers_missing_backup_assignment():
    def extract(ctx):
        source = ctx["sources"][0]
        primary = dict(fact(source), kind="action", owner="余舟", owner_source_ids=[source["id"]])
        if "validation_error" not in ctx:
            return {"facts": [primary]}
        backup = dict(primary, text="陈远备班。", subject="备班", owner="陈远")
        return {"facts": [primary, backup]}

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, "上线当天由余舟主值班，陈远备班。")
    await runtime.finalize()
    assert {f.owner for f in runtime.knowledge.facts} == {"余舟", "陈远"}


@pytest.mark.parametrize(
    "owner,text,valid",
    [
        ("陈宁", "我负责发布。", True),
        ("陈宁", "我准备培训材料时，会把操作错误画成流程图。", True),
        ("陈宁", "我还会让学生实际走一遍接收流程。", True),
        ("陈宁", "我可以在每个模式旁保留相反证据。", True),
        ("陈宁", "我们讨论了培训方案。", False),
        ("凭空姓名", "我负责发布。", False),
        ("陈宁", "首次观察安排在十月十二日上午九点。", False),
    ],
)
async def test_owner_reference_must_contain_name_or_first_person_speaker(owner, text, valid):
    def extract(ctx):
        source = ctx["sources"][0]
        return {
            "facts": [
                dict(fact(source), kind="action", owner=owner, owner_source_ids=[source["id"]])
            ]
        }

    runtime, _ = engine(Scripted(extract=extract))
    await runtime.append(
        TranscriptChunk(
            chunk_id="c1",
            text=text,
            speaker="陈宁",
            seq=1,
            start=0,
            end=len(text),
        )
    )
    if valid:
        await runtime.finalize()
        assert runtime.knowledge.facts[0].owner == "陈宁"
    else:
        with pytest.raises(ModelFailure, match="owner_not_in_cited_source"):
            await runtime.finalize()
        assert runtime.stream.consumed == 0


async def test_explicit_first_person_promise_requires_assignment_and_can_repair():
    def extract(ctx):
        source = ctx["sources"][0]
        if "validation_error" not in ctx:
            return {"facts": [dict(fact(source), kind="proposal", status="proposed")]}
        return {
            "facts": [
                dict(fact(source), kind="action", owner="陈宁", owner_source_ids=[source["id"]])
            ]
        }

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    text = "我还会让学生实际走一遍接收流程。"
    await runtime.append(
        TranscriptChunk(chunk_id="c1", text=text, speaker="陈宁", seq=1, start=0, end=len(text))
    )
    await runtime.finalize()
    assert runtime.knowledge.facts[0].owner == "陈宁"
    assert sum(task == "extract" for task, _ in model.calls) == 2


async def test_unapproved_first_person_proposal_is_not_forced_into_assignment():
    def extract(ctx):
        return {"facts": [dict(fact(ctx["sources"][0]), kind="proposal", status="proposed")]}

    runtime, _ = engine(Scripted(extract=extract))
    text = "我会负责发布，但这只是建议，尚未批准。"
    await runtime.append(
        TranscriptChunk(chunk_id="c1", text=text, speaker="陈宁", seq=1, start=0, end=len(text))
    )
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "proposed"
    assert runtime.knowledge.facts[0].owner == ""


async def test_chapter_fallback_keeps_structured_action():
    def extract(ctx):
        s = ctx["sources"][0]
        f = fact(s, owner="李明", due="周四", owner_source_ids=[s["id"]], due_source_ids=[s["id"]])
        f["kind"] = "action"
        return {"facts": [dict(f, text=f["text"] + f"事项{i}") for i in range(8)]}

    def fail(_):
        raise ModelFailure("response.incomplete:max_output_tokens")

    runtime, _ = engine(Scripted(extract=extract, chapter=fail), body_max_items=6)
    await append(runtime, "李明周四完成验收。")
    await runtime.finalize()
    draft = runtime.knowledge.draft
    assert draft.delivery_warnings
    assert "李明" in draft.markdown and "周四" in draft.markdown
    assert draft.checklist[0].owner == "李明"


async def test_long_input_bounded_requests_complete_coverage():
    model = Scripted(
        extract=lambda c: {
            "facts": [],
            "ignored": [{"source_id": s["id"], "reason": "测试无事实占位"} for s in c["sources"]],
        }
    )
    runtime, _ = engine(model, batch_tokens=256, source_tokens=128)
    for _ in range(8):
        await append(runtime, "测试无事实占位。" * 100)
    await runtime.finalize()
    assert runtime.stream.consumed == runtime.stream.cursor
    assert len(runtime.stream.dispositions) == len(runtime.stream.sources)
    assert len(model.calls) < 120
    for task, payload in model.calls:
        assert runtime.context.fits(
            task, payload, __import__("meetnote.domain.results", fromlist=["Extraction"]).Extraction
        )


async def test_cache_validated_results_and_dependency_changes():
    model = Scripted()
    runtime, _ = engine(model)
    from meetnote.domain.results import ChapterSelection

    payload = {"topic": "测试", "items": [{"text": "事实", "fact_ids": ["f1"]}]}
    await runtime.context.invoke("chapter", payload, ChapterSelection)
    await runtime.context.invoke("chapter", payload, ChapterSelection)
    assert len(model.calls) == 1
    assert runtime.context.stats["cache_hits"] == 1
    payload["items"][0]["text"] = "修订事实"
    await runtime.context.invoke("chapter", payload, ChapterSelection)
    assert len(model.calls) == 2


def test_contract_requires_evidence():
    with pytest.raises(ValueError):
        FactInput(
            text="无证据",
            kind="fact",
            topic="测试",
            subject="事项",
            status="confirmed",
            source_ids=[],
        )


def test_exact_claim_with_shared_evidence_allows_subject_label_expansion_but_not_other_objects():
    from meetnote.domain.results import Fact
    from meetnote.engine.runtime import same_fact

    first = Fact(**fact({"id": "s1.0", "text": "采购预算原定十万元。"}), id="f000001")
    renamed = first.model_copy(
        update={"subject": "采购预算原定金额", "source_ids": ["s1.0", "s2.0"]}
    )
    assert same_fact(first, renamed)
    assert not same_fact(first, renamed.model_copy(update={"text": "采购预算改为二十万元。"}))
    ambiguous = first.model_copy(update={"text": "验收通过", "subject": "甲设备"})
    assert not same_fact(ambiguous, ambiguous.model_copy(update={"subject": "乙设备"}))


async def test_same_batch_revisions_and_duplicate_remapping():
    def extract(ctx):
        sources = ctx["sources"]
        return {"facts": [fact(sources[0]), fact(sources[0]), fact(sources[1])]}

    def reconcile(ctx):
        assert ctx["allowed_pairs"]
        new_id, old_id = ctx["allowed_pairs"][-1]
        new = next(f for f in ctx["new"] if f["id"] == new_id)
        return {
            "relations": [
                {
                    "new_id": new_id,
                    "old_id": old_id,
                    "kind": "supersedes",
                    "quote": new["text"],
                    "reason": "明确改口",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "采购预算十万元。")
    await append(runtime, "采购预算改为二十万元。")
    await runtime.finalize()
    assert len(runtime.knowledge.facts) == 2
    assert runtime.knowledge.facts[0].status == "superseded"
    assert runtime.knowledge.facts[1].status == "confirmed"


async def test_unresolved_fact_can_be_resolved_without_question_kind():
    def extract(ctx):
        f = fact(ctx["sources"][0])
        if "待核实" in f["text"]:
            f["status"] = "unresolved"
        return {"facts": [f]}

    def reconcile(ctx):
        n, o = ctx["allowed_pairs"][0]
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "resolves",
                    "quote": ctx["new"][0]["text"],
                    "reason": "报价得到确认",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "采购报价是否含税待核实。")
    await runtime.cycle("flush")
    await append(runtime, "确认采购报价含税。")
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "resolved"


async def test_explicit_old_arrangement_quote_recalls_differently_named_fact():
    def extract(ctx):
        source = ctx["sources"][0]
        old = "撤销" not in source["text"]
        return {
            "facts": [
                dict(
                    fact(source),
                    kind="action" if old else "decision",
                    subject="交付任务" if old else "采集规模",
                )
            ]
        }

    def reconcile(ctx):
        n, o = ctx["allowed_pairs"][0]
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "supersedes",
                    "quote": ctx["new"][0]["text"],
                    "reason": "明确引用并撤销旧安排",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "计划采集60份水样，由何静负责。")
    await runtime.cycle("flush")
    await append(runtime, "撤销原来“计划采集60份水样”的安排，最终采集48份水样。")
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "superseded"
    assert runtime.knowledge.facts[1].status == "confirmed"


async def test_explicit_conflict_survives_different_attribute_labels():
    def extract(ctx):
        source = ctx["sources"][0]
        label = "合同价格含税" if "甲方" in source["text"] else "合同价格税负"
        return {"facts": [dict(fact(source), status="unresolved", subject=label)]}

    def reconcile(ctx):
        n, o = ctx["allowed_pairs"][0]
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "conflicts",
                    "quote": ctx["new"][0]["text"],
                    "reason": "双方明示未达成一致",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "甲方认为合同价格含税。")
    await runtime.cycle("flush")
    await append(runtime, "乙方认为合同价格不含税，双方未达成一致。")
    await runtime.finalize()
    assert {f.status for f in runtime.knowledge.facts} == {"conflicted"}


@pytest.mark.parametrize("recover", [True, False])
async def test_incomplete_single_revision_retries_once_and_never_silently_commits(recover):
    calls = 0

    def reconcile(ctx):
        nonlocal calls
        calls += 1
        if not recover or calls == 1:
            raise ModelFailure("response_incomplete:whitespace_loop")
        n, o = ctx["allowed_pairs"][0]
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "supersedes",
                    "quote": ctx["new"][0]["text"],
                    "reason": "预算明确更正",
                }
            ]
        }

    runtime, _ = engine(
        Scripted(extract=lambda c: {"facts": [fact(s) for s in c["sources"]]}, reconcile=reconcile)
    )
    await append(runtime, "采购预算十万元。")
    await runtime.cycle("flush")
    before = runtime.stream.consumed
    await append(runtime, "采购预算正式改为二十万元。")
    if recover:
        await runtime.finalize()
        assert runtime.knowledge.facts[0].status == "superseded"
    else:
        with pytest.raises(ModelFailure, match="incomplete"):
            await runtime.finalize()
        assert runtime.stream.consumed == before
        assert len(runtime.knowledge.facts) == 1
    assert calls == 2


async def test_chapter_pages_do_not_drop_checklist_facts():
    runtime, _ = engine(batch_tokens=512, source_tokens=128)
    for i in range(30):
        await append(runtime, f"独立事项{i}：第{i}组确认第{i}项验收。")
    await runtime.finalize()
    draft = runtime.knowledge.draft
    assert len(draft.checklist) == 30
    assert sum(len(section.items) for section in draft.sections) <= 18
    assert set(draft.evidence_trace) == {f.id for f in runtime.knowledge.summaries}
    assert all(f.text.replace("[模拟] ", "") in draft.markdown for f in draft.checklist)


async def test_large_ledger_uses_multiple_bounded_selection_levels():
    from meetnote.domain.results import ChapterSelection, Fact

    model = Scripted()
    runtime, _ = engine(model, input_token_budget=4096)
    runtime.knowledge.facts = [
        Fact(
            id=f"f{i:06d}",
            text=f"事项{i}：" + "保留数字、条件与不确定性。" * 4,
            kind="constraint",
            subject=f"独立对象{i}",
            topic=f"议题{i}",
            status="confirmed",
            source_ids=[f"s{i}.0"],
        )
        for i in range(120)
    ]
    await runtime.compose()
    assert len(runtime.knowledge.draft.checklist) == 120
    assert sum(len(s.items) for s in runtime.knowledge.draft.sections) <= 18
    assert len(model.calls) > 2
    assert all(len(payload["items"]) <= 24 for _, payload in model.calls)
    assert all(
        item["text"].startswith("[constraint/confirmed]")
        for _, payload in model.calls
        for item in payload["items"]
    )
    assert all(
        runtime.context.fits(task, payload, ChapterSelection) for task, payload in model.calls
    )


async def test_brief_keeps_explicit_owner_for_first_person_statement():
    from meetnote.domain.results import Fact

    runtime, _ = engine()
    runtime.knowledge.facts = [
        Fact(
            id="f000001",
            text="我周四交报告。",
            kind="action",
            status="confirmed",
            topic="报告",
            subject="提交报告",
            owner="陈宁",
            due="周四",
            source_ids=["s1.0"],
            owner_source_ids=["s1.0"],
            due_source_ids=["s1.0"],
        )
    ]
    await runtime.compose()
    item = runtime.knowledge.draft.sections[0].items[0]
    assert "负责人：陈宁" in item.text
    assert item.text.count("周四") == 1


async def test_field_audit_removes_unrelated_time_without_dropping_fact():
    def extract(ctx):
        source = ctx["sources"][0]
        return {"facts": [dict(fact(source), kind="action", owner="顾珊", due="今天")]}

    def fields(ctx):
        return {
            c["id"]: {
                "owner_supported": True,
                "due_supported": False,
                "reason": "今天修饰别猜，不是核实期限",
            }
            for c in ctx["claims"]
        }

    runtime, events = engine(Scripted(extract=extract, fields=fields))
    await append(runtime, "顾珊去核原工单，今天别猜。")
    await runtime.finalize()
    saved = runtime.knowledge.facts[0]
    assert saved.owner == "顾珊"
    assert saved.due == "" and saved.due_source_ids == []
    assert saved.text == "顾珊去核原工单，今天别猜。"
    assert runtime.stream.consumed == runtime.stream.cursor
    assert any(kind == "fields.corrected" for kind, _ in events)


async def test_field_audit_failure_cannot_commit_unverified_fields():
    def extract(ctx):
        return {"facts": [dict(fact(ctx["sources"][0]), kind="action", owner="陈宁")]}

    def fields(ctx):
        raise ModelFailure("model_unavailable")

    runtime, _ = engine(Scripted(extract=extract, fields=fields))
    await append(runtime, "陈宁负责发布。")
    with pytest.raises(ModelFailure, match="model_unavailable"):
        await runtime.finalize()
    assert runtime.stream.consumed == 0
    assert not runtime.knowledge.facts


async def test_invalid_outputs_do_not_enter_cache():
    runtime, _ = engine(Scripted(extract=lambda c: {"facts": []}))
    await append(runtime, "必须处理此证据。")
    with pytest.raises(ModelFailure):
        await runtime.finalize()
    assert not runtime.context.cache


async def test_repeated_unanswered_question_does_not_invoke_revision_model():
    def extract(ctx):
        result = []
        for source in ctx["sources"]:
            f = fact(source)
            f.update(kind="question", status="unresolved")
            result.append(f)
        return {"facts": result}

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    await append(runtime, "报价含税吗？是否含税仍然待确认？")
    await runtime.finalize()
    assert all(task != "reconcile" for task, _ in model.calls)
    assert all(f.status == "unresolved" for f in runtime.knowledge.facts)


async def test_unrelated_assignments_do_not_become_conflicts():
    def extract(ctx):
        values = []
        for source, subject in zip(ctx["sources"], ["回归测试", "发布部署"]):
            f = fact(source)
            f.update(kind="action", subject=subject)
            values.append(f)
        return {"facts": values}

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    await append(runtime, "小李负责回归。")
    await append(runtime, "小王负责发布。")
    await runtime.finalize()
    assert all(task != "reconcile" for task, _ in model.calls)
    assert all(f.status == "confirmed" for f in runtime.knowledge.facts)


async def test_shared_product_prefix_does_not_make_unrelated_facts_revision_candidates():
    def extract(ctx):
        return {
            "facts": [
                dict(fact(source), subject="溪桥工单平台" + subject)
                for source, subject in zip(
                    ctx["sources"], ["客户反馈分类", "页面状态与返回行为", "演示账号权限"]
                )
            ]
        }

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    for text in ["客户反馈分两类。", "返回保留页面状态。", "普通坐席不具备管理员权限。"]:
        await append(runtime, text)
    await runtime.finalize()
    assert not any(task == "reconcile" for task, _ in model.calls)


@pytest.mark.parametrize("incomplete", [False, True])
async def test_relation_output_work_is_bounded_even_when_all_subjects_match(incomplete):
    def reconcile(ctx):
        if incomplete and len(ctx["allowed_pairs"]) > 1:
            raise ModelFailure("response_incomplete:whitespace_loop")
        return {"relations": []}

    model = Scripted(
        extract=lambda c: {"facts": [fact(s) for s in c["sources"]]}, reconcile=reconcile
    )
    runtime, _ = engine(model)
    for i in range(12):
        await append(runtime, f"采购预算改为第{i}版。")
    await runtime.finalize()
    pages = [payload for task, payload in model.calls if task == "reconcile"]
    assert len(pages) > 1
    assert all(len(page["allowed_pairs"]) <= 6 for page in pages)
    for page in pages:
        assert {f["id"] for f in page["new"]} == {pair[0] for pair in page["allowed_pairs"]}
        assert {f["id"] for f in page["candidates"]} == {pair[1] for pair in page["allowed_pairs"]}
    verified = [p for p in pages if not incomplete or len(p["allowed_pairs"]) == 1]
    pairs = [tuple(pair) for page in verified for pair in page["allowed_pairs"]]
    assert len(set(pairs)) == len(pairs) == 66


async def test_literal_field_evidence_is_grounded_without_extra_inference():
    def extract(ctx):
        f = fact(ctx["sources"][0], owner="陈宁", due="周四")
        f["kind"] = "action"
        return {"facts": [f]}

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    await append(runtime, "陈宁负责周四完成验收。")
    await runtime.finalize()
    f = runtime.knowledge.facts[0]
    assert f.owner_source_ids == f.source_ids
    assert f.due_source_ids == f.source_ids
    assert sum(task == "extract" for task, _ in model.calls) == 1


async def test_complementary_information_is_not_committed_as_conflict():
    def extract(ctx):
        return {"facts": [fact(source) for source in ctx["sources"]]}

    def reconcile(ctx):
        new_id, old_id = ctx["allowed_pairs"][0]
        new = next(f for f in ctx["new"] if f["id"] == new_id)
        return {
            "relations": [
                {
                    "new_id": new_id,
                    "old_id": old_id,
                    "kind": "conflicts",
                    "quote": new["text"],
                    "reason": "模型错误地认为信息互补是冲突",
                }
            ]
        }

    runtime, events = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "软件授权绑定电脑。")
    await append(runtime, "软件授权需要单独核对。")
    await runtime.finalize()
    assert all(f.status == "confirmed" for f in runtime.knowledge.facts)
    assert not runtime.knowledge.revisions
    assert not any(kind == "model.started" and data["task"] == "reconcile" for kind, data in events)


@pytest.mark.parametrize(
    "old,new",
    [
        ("汇率使用预算版比较，实际汇率差异需保留来源。", "明确一下，预算假设不是未来汇率的保证。"),
        ("必须保留原始验收记录。", "不得取消保留原始验收记录的要求。"),
    ],
)
async def test_clarification_and_negated_cancellation_cannot_overwrite_confirmed_fact(old, new):
    model = Scripted(extract=lambda c: {"facts": [fact(s) for s in c["sources"]]})
    runtime, _ = engine(model)
    await append(runtime, old)
    await runtime.cycle("flush")
    await append(runtime, new)
    await runtime.finalize()
    assert all(f.status == "confirmed" for f in runtime.knowledge.facts)
    assert not any(task == "reconcile" for task, _ in model.calls)


async def test_withdrawn_proposal_recalls_original_despite_different_subject_names():
    def extract(ctx):
        source = ctx["sources"][0]
        old = "撤回" not in source["text"]
        return {
            "facts": [
                dict(
                    fact(source),
                    kind="proposal" if old else "decision",
                    status="proposed" if old else "confirmed",
                    subject="覆盖初稿" if old else "分层实施",
                )
            ]
        }

    def reconcile(ctx):
        n, o = ctx["allowed_pairs"][0]
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "retracts",
                    "quote": ctx["new"][0]["text"],
                    "reason": "明确撤回草案",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "先提一个草案，安全库存按四十五天覆盖，大家先讨论，不是最终批准。")
    await runtime.cycle("flush")
    await append(
        runtime, "撤回刚开会四十五天的库存草案，按物料分层讨论，不搞一个天数覆盖所有物料。"
    )
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "retracted"


async def test_default_twelve_source_batch_can_split_all_the_way_to_single_sources():
    def extract(ctx):
        if len(ctx["sources"]) > 1:
            raise ModelFailure("response_incomplete")
        return {"facts": [fact(ctx["sources"][0])]}

    runtime, _ = engine(Scripted(extract=extract), batch_tokens=4096)
    for i in range(12):
        await append(runtime, f"独立片段{i}。")
    await runtime.finalize()
    assert len(runtime.knowledge.facts) == 12
    assert len(runtime.stream.dispositions) == 12
    assert runtime.stream.consumed == runtime.stream.cursor


def test_positive_relation_schema_requires_non_null_evidence():
    from pydantic import ValidationError

    from meetnote.domain.results import relation_schema

    schema = relation_schema({"p0": ["s2.0#0"]}, {"p0": ["none", "supersedes"]})
    with pytest.raises(ValidationError):
        schema.model_validate(
            {"p0": {"kind": "supersedes", "evidence_id": None, "reason": "缺证据"}}
        )
    schema.model_validate({"p0": {"kind": "none", "evidence_id": None, "reason": "无修订"}})


async def test_source_preserves_short_utterance_and_its_clauses():
    runtime, _ = engine()
    await append(runtime, "预算尚未确认。需要核对合同附件。")
    assert len(runtime.stream.sources) == 1
    assert runtime.stream.sources[0].id == "s1.0"


@pytest.mark.parametrize("include_new", [True, False])
async def test_context_echo_is_not_new_fact_and_cannot_hide_missing_sources(include_new):
    model = Scripted(extract=lambda c: {"facts": [fact(s) for s in c["sources"]]})
    runtime, _ = engine(model)
    await append(runtime, "上轮已经记录的事实。")
    await runtime.cycle("flush")
    consumed = runtime.stream.consumed
    model.handlers["extract"] = lambda c: {
        "facts": [fact(s) for s in c["context_sources"] + (c["sources"] if include_new else [])]
    }
    await append(runtime, "本轮新增的事实。")
    if include_new:
        await runtime.finalize()
        assert len(runtime.knowledge.facts) == 2
        assert not runtime.knowledge.revisions
        assert sum(task == "extract" for task, _ in model.calls) == 2
    else:
        with pytest.raises(ModelFailure, match="source_coverage_incomplete"):
            await runtime.finalize()
        assert runtime.stream.consumed == consumed


async def test_brief_reserves_work_items_amid_many_background_facts():
    from meetnote.domain.results import Fact

    # 模拟选择器总取前六条，关键事项也不能被早期背景挤掉。
    runtime, _ = engine(Scripted())
    runtime.knowledge.facts = [
        Fact(
            id=f"f{i:06d}",
            text=f"背景讨论{i}",
            kind="fact",
            status="confirmed",
            topic="背景",
            subject=f"背景{i}",
            source_ids=[f"s{i}.0"],
        )
        for i in range(100)
    ]
    for i, kind in enumerate(("action", "decision", "question", "constraint"), start=100):
        runtime.knowledge.facts.append(
            Fact(
                id=f"f{i:06d}",
                text=f"关键事项{i}",
                kind=kind,
                status="unresolved" if kind == "question" else "confirmed",
                topic="工作",
                subject=f"事项{i}",
                source_ids=[f"s{i}.0"],
            )
        )
    await runtime.compose()
    ids = {
        fid
        for section in runtime.knowledge.draft.sections
        for item in section.items
        for fid in item.fact_ids
    }
    assert {f"f{i:06d}" for i in range(100, 104)} <= ids
    assert len(ids) <= 18
    assert len(runtime.knowledge.draft.checklist) == 104


async def test_brief_keeps_late_revision_separate_from_many_old_decisions():
    from meetnote.domain.results import Fact

    runtime, _ = engine(Scripted())
    runtime.knowledge.facts = [
        Fact(
            id=f"f{i:06d}",
            text=f"决定{i}",
            kind="decision",
            status="confirmed",
            topic="安排",
            subject=f"事项{i}",
            source_ids=[f"s{i}.0"],
        )
        for i in range(100)
    ]
    runtime.knowledge.facts[0] = runtime.knowledge.facts[0].model_copy(
        update={"status": "superseded", "related_ids": ["f000099"]}
    )
    await runtime.compose()
    ids = {
        fid
        for section in runtime.knowledge.draft.sections
        for item in section.items
        for fid in item.fact_ids
    }
    assert "f000099" in ids
    assert "f000000" not in ids
    assert len(ids) <= 18


async def test_short_multi_sentence_failure_splits_without_losing_boundaries():
    def extract(ctx):
        source = ctx["sources"][0]
        if source["text"].count("。") > 1:
            raise ModelFailure("task_validation_failed:multiple_assignments")
        return {"facts": [fact(source)]}

    runtime, _ = engine(Scripted(extract=extract))
    original = "甲完成核实。乙负责备班。丙完成复检。"
    await append(runtime, original)
    await runtime.finalize()
    assert "".join(s.text for s in runtime.stream.sources) == original
    assert len(runtime.stream.dispositions) == 3
    assert len(runtime.knowledge.facts) == 3
    assert runtime.stream.consumed == len(original)
    assert all(s.text.endswith("。") for s in runtime.stream.sources)


async def test_explicit_deadline_stays_attached_to_assigned_person():
    attempts = 0

    def extract(ctx):
        nonlocal attempts
        attempts += 1
        source = ctx["sources"][0]
        due = "五月九日" if attempts == 1 else "五月二日"
        return {"facts": [dict(fact(source), kind="action", owner="林洁", due=due)]}

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, "评审改为五月九日，仍由林洁负责，交付期限仍为五月二日。")
    await runtime.finalize()
    assert attempts == 2
    assert runtime.knowledge.facts[0].due == "五月二日"
    assert runtime.knowledge.facts[0].owner == "林洁"


@pytest.mark.parametrize(
    "prefix",
    [
        "原来由林洁负责，交付期限为五月二日，该安排作废。",
        "旧资料写着“由林洁负责，交付期限为五月二日”。",
        "建议由林洁负责，交付期限为五月二日，尚未批准。",
    ],
)
async def test_deadline_guard_does_not_enforce_history_or_proposals(prefix):
    def extract(ctx):
        return {
            "facts": [dict(fact(ctx["sources"][0]), kind="action", owner="林洁", due="五月十日")]
        }

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, prefix + "林洁确认五月十日提交新的验收报告。")
    await runtime.finalize()
    assert runtime.knowledge.facts[0].due == "五月十日"


@pytest.mark.parametrize("debug_days", [14, 30])
async def test_quoted_revision_cannot_overwrite_another_log_type(debug_days):
    def reconcile(ctx):
        return {
            "relations": [
                {
                    "new_id": new,
                    "old_id": old,
                    "kind": "supersedes",
                    "quote": ctx["new"][0]["text"],
                    "reason": "明确替代",
                }
                for new, old in ctx["allowed_pairs"]
            ]
        }

    runtime, _ = engine(
        Scripted(
            extract=lambda c: {
                "facts": [dict(fact(s), subject="日志保留期") for s in c["sources"]]
            },
            reconcile=reconcile,
        )
    )
    for text in [
        "操作审计日志保留30天。",
        f"调试日志保留{debug_days}天。",
        f"此前“操作审计日志保留30天”撤销，最终操作审计日志保留60天，调试日志仍为{debug_days}天。",
    ]:
        await append(runtime, text)
        await runtime.cycle("flush")
    assert runtime.knowledge.facts[0].status == "superseded"
    assert runtime.knowledge.facts[1].status == "confirmed"


async def test_paraphrased_quote_and_short_object_recall_candidates():
    from meetnote.domain.results import Fact
    from meetnote.engine.runtime import eligible_pair

    def value(fid, text, subject):
        return Fact(
            id=fid,
            text=text,
            subject=subject,
            topic="事项",
            kind="fact",
            status="confirmed",
            source_ids=[fid],
        )

    old = value("f1", "旧口径由经费负责人落实为每月12万元云资源预算。", "经费落实")
    new = value("f2", "此前“本期云资源预算每月12万元”撤销，改为每月9万元。", "预算上限")
    assert eligible_pair(new, old)
    old = value("f1", "演练原定五月一日交付。", "演练和切换准备")
    new = value("f2", "演练交付改为五月三日。", "演练交付期限")
    assert eligible_pair(new, old)


async def test_action_cannot_replace_task_content_with_an_empty_reference():
    calls = 0

    def extract(ctx):
        nonlocal calls
        calls += 1
        text = (
            "林洁负责这项落实，交付到五月二日。"
            if calls == 1
            else "林洁落实主键重复率必须为零、迟到率单独监控的要求。"
        )
        return {
            "facts": [
                dict(
                    fact(ctx["sources"][0]), text=text, kind="action", owner="林洁", due="五月二日"
                )
            ]
        }

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, "主键重复率必须为零，迟到率单独监控。林洁负责这项落实，交付到五月二日。")
    await runtime.finalize()
    assert calls == 1
    assert "重复率必须为零" in runtime.knowledge.facts[0].text


async def test_source_records_cannot_exchange_neighbor_evidence_ids():
    class Misbound(MockClient):
        attempts = 0

        async def generate(self, task, instructions, context, schema):
            if task != "extract":
                return await super().generate(task, instructions, context, schema)
            self.attempts += 1
            sources = context["sources"]
            return schema.model_validate(
                {
                    s["id"]: {
                        "facts": [
                            {
                                "category": "已确认事实",
                                "text": s["text"],
                                "topic": "测试",
                                "subject": s["text"],
                                "source_ids": [
                                    sources[1 - i]["id"] if self.attempts == 1 else s["id"]
                                ],
                            }
                        ],
                        "ignored_reason": "",
                    }
                    for i, s in enumerate(sources)
                }
            )

    model = Misbound()
    runtime, _ = engine(model)
    await append(runtime, "甲场地关闭。")
    await append(runtime, "乙场地开放。")
    await runtime.finalize()
    assert model.attempts == 2
    for fact_value in runtime.knowledge.facts:
        assert runtime.source_index[fact_value.source_ids[0]].text == fact_value.text


@pytest.mark.parametrize(
    "text",
    [
        "普通下雨不取消活动。",
        "不因普通下雨自动取消。",
        "不是看到普通下雨就自动取消。",
        "并非所有活动都要取消。",
    ],
)
def test_negated_cancellation_does_not_open_revision(text):
    from meetnote.engine.runtime import relation_signals

    assert not relation_signals(text) & {"supersedes", "retracts"}
    assert "supersedes" in relation_signals("不是取消，而是改为周二。")


async def test_revision_recall_uses_raw_quote_omitted_from_extracted_claim():
    def extract(ctx):
        source = ctx["sources"][0]
        f = fact(source)
        if "撤销" in source["text"]:
            f.update(text="先做5人的任务测试。", subject="新实验范围")
        else:
            f.update(subject="可用性验证准备")
        return {"facts": [f]}

    def reconcile(ctx):
        return {
            "relations": [
                {
                    "new_id": ctx["new"][0]["id"],
                    "old_id": ctx["candidates"][0]["id"],
                    "kind": "supersedes",
                    "quote": "撤销旧安排",
                    "reason": "原文明示撤销",
                }
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "先开展两周可用性验证。")
    await runtime.cycle("flush")
    await append(runtime, "撤销“先开展两周可用性验证”，现在确认先做5人的任务测试。")
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "superseded"


async def test_multiple_supporting_revisions_do_not_fabricate_conflict():
    def extract(ctx):
        source = ctx["sources"][0]
        if "撤销" not in source["text"]:
            return {"facts": [fact(source)]}
        return {
            "facts": [
                dict(fact(source), text="预算改为二十万元。"),
                dict(fact(source), text="撤销旧预算十万元。"),
            ]
        }

    def reconcile(ctx):
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "supersedes",
                    "quote": "预算改为二十万元",
                    "reason": "同次修订的两条说明",
                }
                for n, o in ctx["allowed_pairs"]
                if o == "f000001"
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "预算为十万元。")
    await runtime.cycle("flush")
    await append(runtime, "撤销旧预算十万元，预算改为二十万元。")
    await runtime.finalize()
    assert runtime.knowledge.facts[0].status == "superseded"
    assert len(runtime.knowledge.facts[0].related_ids) == 2
    assert all(f.status != "conflicted" for f in runtime.knowledge.facts)


async def test_same_evidence_owner_reaffirmation_cannot_replace_new_decision():
    def extract(ctx):
        source = ctx["sources"][0]
        return {
            "facts": [
                dict(
                    fact(source),
                    text="撤销旧教室安排，改为二楼201室。",
                    subject="教室安排",
                    kind="decision",
                ),
                dict(
                    fact(source),
                    text="仍由林洁负责教室安排，期限周五不变。",
                    subject="教室安排",
                    kind="action",
                    owner="林洁",
                    due="周五",
                ),
            ]
        }

    def reconcile(ctx):
        return {
            "relations": [
                {
                    "new_id": n,
                    "old_id": o,
                    "kind": "supersedes",
                    "quote": "撤销旧教室安排",
                    "reason": "模拟错误覆盖",
                }
                for n, o in ctx["allowed_pairs"]
            ]
        }

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "撤销旧教室安排，改为二楼201室。仍由林洁负责教室安排，期限周五不变。")
    await runtime.finalize()
    assert all(f.status == "confirmed" for f in runtime.knowledge.facts)
    assert "二楼201室" in runtime.knowledge.draft.markdown


async def test_grounded_subject_completes_anaphoric_assignment_without_retry():
    def extract(ctx):
        return {
            "facts": [
                dict(
                    fact(ctx["sources"][0]),
                    text="仍由何静负责，交付期限仍为十月十八日。",
                    subject="水样采集交付负责人及期限",
                    kind="action",
                    owner="何静",
                    due="十月十八日",
                )
            ]
        }

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    await append(runtime, "确认采集48份水样。仍由何静负责，交付期限仍为十月十八日。")
    await runtime.finalize()
    assert sum(task == "extract" for task, _ in model.calls) == 1
    assert runtime.knowledge.facts[0].text.startswith("确认采集48份水样。")
    assert runtime.knowledge.facts[0].owner == "何静"


async def test_pending_decision_constraint_requests_correct_classification():
    attempts = 0

    def extract(ctx):
        nonlocal attempts
        attempts += 1
        f = fact(ctx["sources"][0])
        if attempts == 1:
            f.update(text="不作决定", kind="constraint")
        else:
            f.update(text="是否增加验收尚未决定。", kind="question", status="unresolved")
        return {"facts": [f]}

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, "先讨论是否增加验收，不作决定。")
    await runtime.finalize()
    assert attempts == 2
    assert runtime.knowledge.facts[0].status == "unresolved"
    assert "不作决定" not in runtime.knowledge.draft.markdown


async def test_reaffirmed_uncertainty_cannot_resolve_old_question_using_other_confirmation():
    from meetnote.domain.results import Fact

    def extract(ctx):
        f = fact(ctx["sources"][0])
        f.update(subject="试点名单", kind="question", status="unresolved")
        return {"facts": [f]}

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    await append(runtime, "试点名单待客户成功团队确认。")
    await runtime.cycle("flush")
    await append(runtime, "正式确认本轮只做权限。试点名单还没有确定。")
    # 独立验证修订边界，即便上游误标为已确认，也不能借用别的确认句关闭旧问题。
    claim = Fact(
        id="f000002",
        text="试点名单还没有确定。",
        topic="试点",
        subject="试点名单",
        kind="fact",
        status="confirmed",
        source_ids=["s2.0"],
    )
    assert await runtime.reconcile([claim]) == []
    assert runtime.knowledge.facts[0].status == "unresolved"
    assert not any(task == "reconcile" for task, _ in model.calls)


async def test_assignment_object_after_comma_is_complete():
    def extract(ctx):
        return {
            "facts": [
                dict(
                    fact(ctx["sources"][0]),
                    text="何静负责，十月十二日完成验收增加事项。",
                    subject="验收增加",
                    kind="action",
                    owner="何静",
                    due="十月十二日",
                )
            ]
        }

    model = Scripted(extract=extract)
    runtime, _ = engine(model)
    await append(runtime, "正式决定增加验收，何静负责，十月十二日完成。")
    await runtime.finalize()
    assert sum(task == "extract" for task, _ in model.calls) == 1
    assert runtime.knowledge.facts[0].owner == "何静"


async def test_conditional_first_person_executor_requires_speaker_owner():
    attempts = 0

    def extract(ctx):
        nonlocal attempts
        attempts += 1
        f = fact(ctx["sources"][0])
        if attempts > 1:
            f.update(kind="action", owner="丁珂", owner_source_ids=[ctx["sources"][0]["id"]])
        return {"facts": [f]}

    runtime, _ = engine(Scripted(extract=extract))
    text = "回滚由我执行，但必须得到周宁确认。"
    await runtime.append(
        TranscriptChunk(chunk_id="u1", seq=1, text=text, speaker="丁珂", start=0, end=len(text))
    )
    await runtime.finalize()
    assert attempts == 2
    assert runtime.knowledge.facts[0].owner == "丁珂"
    assert "周宁确认" in runtime.knowledge.facts[0].text


@pytest.mark.parametrize(
    "text",
    [
        "回滚不由我执行。",
        "回滚不是由我执行。",
        "回滚不再由我执行。",
        "回滚原来由我执行，现在改由张三负责。",
        "如果由我执行回滚，需另行批准。",
        "文档曾写着“回滚由我执行”，今天不作分工。",
    ],
)
async def test_negative_hypothetical_or_quoted_execution_is_not_assignment(text):
    runtime, _ = engine(Scripted(extract=lambda ctx: {"facts": [fact(ctx["sources"][0])]}))
    await runtime.append(
        TranscriptChunk(chunk_id="u1", seq=1, text=text, speaker="丁珂", start=0, end=len(text))
    )
    await runtime.finalize()
    assert not runtime.knowledge.facts[0].owner


async def test_literal_first_person_assignment_bypasses_model_field_audit():
    def extract(ctx):
        s = ctx["sources"][0]
        return {
            "facts": [
                dict(
                    fact(s),
                    text="回滚由丁珂执行，但须周宁确认。",
                    kind="action",
                    owner="丁珂",
                    owner_source_ids=[s["id"]],
                )
            ]
        }

    def fields(ctx):
        pytest.fail("逐字可证且没有期限的分工不应再调用模型核验")

    runtime, _ = engine(Scripted(extract=extract, fields=fields))
    text = "回滚由我执行，但须周宁确认。"
    await runtime.append(
        TranscriptChunk(chunk_id="u1", seq=1, text=text, speaker="丁珂", start=0, end=len(text))
    )
    await runtime.finalize()
    assert runtime.knowledge.facts[0].owner == "丁珂"


@pytest.mark.parametrize(
    "text",
    [
        "不是回滚由我执行。",
        "回滚不能由我执行。",
        "回滚不应由我执行。",
        "如果有必要，回滚由我执行。",
        "文档写着“回滚由我执行”。",
        "回滚由我执行，但这是原来的安排。",
    ],
)
async def test_literal_owner_proof_excludes_negation_conditions_quotes_and_history(text):
    from meetnote.domain.results import Fact

    runtime, _ = engine()
    await runtime.append(
        TranscriptChunk(chunk_id="u1", seq=1, text=text, speaker="丁珂", start=0, end=len(text))
    )
    claim = Fact(
        id="f1",
        text=text.replace("我", "丁珂"),
        kind="action",
        status="confirmed",
        topic="回滚",
        subject="回滚",
        owner="丁珂",
        source_ids=["s1.0"],
        owner_source_ids=["s1.0"],
    )
    assert not runtime.literal_owner_support(claim)


async def test_anaphoric_assignment_preserves_verbatim_numeric_requirement():
    def extract(ctx):
        source = ctx["sources"][0]
        return {
            "facts": [
                dict(
                    fact(source),
                    text="蒋言负责这项落实，交付到十月二十二日。",
                    subject="关键订单主键重复率与迟到率监控落实",
                    kind="action",
                    owner="蒋言",
                    due="十月二十二日",
                )
            ]
        }

    runtime, _ = engine(Scripted(extract=extract))
    await append(
        runtime,
        "质量要求：关键订单主键重复率必须为零，迟到率单独监控。蒋言负责这项落实，交付到十月二十二日。",
    )
    await runtime.finalize()
    assert "重复率必须为零，迟到率单独监控" in runtime.knowledge.facts[0].text


async def test_pending_schedule_is_not_a_confirmed_decision():
    attempts = 0

    def extract(ctx):
        nonlocal attempts
        attempts += 1
        f = fact(ctx["sources"][0])
        if attempts == 1:
            f.update(kind="decision")
        else:
            f.update(text="具体切换日期仍待最后确认。", kind="question", status="unresolved")
        return {"facts": [f]}

    runtime, _ = engine(Scripted(extract=extract))
    await append(runtime, "具体切换日期仍待最后确认。")
    await runtime.finalize()
    assert attempts == 2
    assert runtime.knowledge.facts[0].status == "unresolved"


async def test_routine_closing_does_not_take_a_brief_slot():
    from meetnote.domain.results import Fact

    runtime, _ = engine()
    runtime.knowledge.facts = [
        Fact(
            id="f1",
            text="今天散会。",
            kind="decision",
            status="confirmed",
            topic="结束",
            subject="散会",
            source_ids=["s1.0"],
        )
    ]
    await runtime.compose()
    assert runtime.knowledge.draft.sections == []
    assert len(runtime.knowledge.draft.checklist) == 1


@pytest.mark.parametrize(
    "text",
    [
        "要求甲。林洁负责这项落实。要求乙。林洁负责这项落实。",
        "文档只是写着‘要求甲。林洁负责这项落实。’",
    ],
)
async def test_anaphoric_context_requires_one_unquoted_assignment(text):
    from meetnote.domain.results import Extraction

    runtime, _ = engine()
    await append(runtime, text)
    claim = FactInput(
        text="林洁负责这项落实。",
        kind="action",
        status="confirmed",
        topic="执行",
        subject="落实",
        owner="林洁",
        source_ids=["s1.0"],
        owner_source_ids=["s1.0"],
    )
    with pytest.raises(ModelFailure, match="assignment_subject_missing"):
        runtime.validate_extraction(Extraction(facts=[claim]), runtime.stream.pending)


@pytest.mark.parametrize("kind", ["fact", "question"])
def test_quoted_previous_value_does_not_hide_answer_to_pending_same_object(kind):
    from meetnote.domain.results import Fact
    from meetnote.engine.runtime import eligible_pair

    old = Fact(
        id="f000001",
        text="具体切换日期仍待最后确认。",
        kind=kind,
        status="unresolved",
        topic="切换",
        subject="切换日期",
        source_ids=["s1.0"],
    )
    new = Fact(
        id="f000002",
        text="此前“切换原计划十月二十八日”撤销，最终切换日期为十一月二日。",
        kind="decision",
        status="confirmed",
        topic="切换",
        subject="切换日期",
        source_ids=["s2.0"],
    )
    assert eligible_pair(new, old)
    unrelated = old.model_copy(update={"subject": "食堂餐标", "text": "食堂餐标仍待确认。"})
    assert not eligible_pair(new, unrelated)


async def test_added_verbatim_context_is_not_a_second_revision_of_same_source_decision():
    def extract(ctx):
        s = ctx["sources"][0]
        return {
            "facts": [
                dict(
                    fact(s),
                    text="此前旧安排撤销，切换改为十一月二日。",
                    subject="切换日期",
                    kind="decision",
                ),
                dict(
                    fact(s),
                    text="许澜继续负责。",
                    subject="切换负责人",
                    kind="action",
                    owner="许澜",
                ),
            ]
        }

    def reconcile(ctx):
        pytest.fail("同一证据上的上下文补全不是对另一个新条目的修订")

    runtime, _ = engine(Scripted(extract=extract, reconcile=reconcile))
    await append(runtime, "此前旧安排撤销，切换改为十一月二日。许澜继续负责。")
    await runtime.finalize()
    assert len(runtime.knowledge.summaries) == 2
    assert any(f.kind == "decision" and f.status == "confirmed" for f in runtime.knowledge.facts)
