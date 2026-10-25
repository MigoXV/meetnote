import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from meetnote.config import Settings
from meetnote.domain.fulltext import DEFAULT_PIPELINE
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ModelFailure
from meetnote.llm.mock import MockClient
from meetnote.sessions.fulltext import FulltextBusy, FulltextService
from meetnote.web.factory import create_app

TEXT = (
    "项目周会确认本周先完成接口联调，由陈宁负责，周五提交测试结果。"
    "预算上限为三万元，不包括设备采购。设备方案仍待审批，不能提前下单。"
    "会上建议增加一次演练，但尚未批准。王敏负责整理风险清单，负责人和期限以会议明确安排为准。"
)


def settings(**kwargs):
    return Settings(_env_file=None, provider="mock", **kwargs)


class RecordingModel(MockClient):
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    async def generate(self, task, instructions, context, schema):
        self.calls.append((task, context))
        if task == self.fail:
            raise ModelFailure("secret upstream URL and key must not be returned")
        return await super().generate(task, instructions, context, schema)


def test_default_fulltext_contract_uses_engine_and_releases_request(monkeypatch):
    runs = []
    original = MeetingEngine.run

    async def run(self, chunks, **options):
        await original(self, chunks, **options)
        runs.append((self.stream.cursor, self.stream.consumed, self.finalized))

    monkeypatch.setattr(MeetingEngine, "run", run)
    app = create_app(settings(), frontend=False)
    with TestClient(app) as client:
        response = client.post("/v1/fulltext/generate", json={"id": "meeting-1", "text": TEXT})
        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"id", "final_abstract", "minutes", "keywords", "next_actions"}
        assert body["keywords"] is None
        assert body["minutes"]["markdown"]
        assert body["next_actions"]["markdown"]
        assert runs == [(len(TEXT), len(TEXT), True)]
        assert client.get("/api/v1/sessions").json() == []
        assert not app.state.fulltext.tasks
        assert client.get("/health").json() == {"status": "ok"}


def test_selected_capabilities_run_in_order_and_read_original_text():
    model = RecordingModel()
    app = create_app(settings(), model=model, frontend=False)
    with TestClient(app) as client:
        body = client.post(
            "/v1/fulltext/generate",
            json={"id": "selected", "text": TEXT, "pipeline": ["polish", "abstract", "keywords"]},
        ).json()
        assert set(body) == {"id", "polished_text", "final_abstract", "keywords"}
        assert [task for task, _ in model.calls] == [
            "fulltext_polish",
            "fulltext_abstract",
            "fulltext_evidence",
        ]
        assert all(payload["text"] == TEXT for _, payload in model.calls[:2])
        assert "".join(source["text"] for source in model.calls[2][1]["sources"]) == TEXT
        # 业务 ID 是关联 ID，不进行幂等去重，也不跨请求复用模型结果。
        client.post(
            "/v1/fulltext/generate", json={"id": "selected", "text": TEXT, "pipeline": ["polish"]}
        )
        assert len(model.calls) == 4


@pytest.mark.parametrize(
    ("update", "status"),
    [
        ({"id": "bad id"}, 400),
        ({"id": ""}, 400),
        ({"text": "  "}, 400),
        ({"text": "文" * 200001}, 413),
        ({"pipeline": []}, 400),
        ({"pipeline": ["minutes", "minutes"]}, 400),
        ({"pipeline": ["unknown"]}, 400),
        ({"extra": True}, 400),
    ],
)
def test_fulltext_validation_does_not_call_model(update, status):
    model = RecordingModel()
    with TestClient(create_app(settings(), model=model, frontend=False)) as client:
        response = client.post("/v1/fulltext/generate", json={"id": "valid", "text": TEXT, **update})
        assert response.status_code == status
        assert not model.calls


@pytest.mark.parametrize("characters", [60001, 61868, 200000])
def test_long_fulltext_admission_preserves_original_request_and_complete_text(
    monkeypatch, characters
):
    text = "会议原文。" * (characters // 5) + "文" * (characters % 5)
    accepted = []
    app = create_app(settings(), frontend=False)

    async def capture(request_id, original, pipeline, **options):
        accepted.append((request_id, original, pipeline))
        return {
            "id": request_id,
            "final_abstract": "摘要",
            "minutes": {"markdown": "纪要"},
            "next_actions": {"markdown": "计划"},
        }

    with TestClient(app) as client:
        monkeypatch.setattr(app.state.fulltext, "run", capture)
        response = client.post("/v1/fulltext/generate", json={"id": "original-app", "text": text})
        assert response.status_code == 200
        assert accepted == [("original-app", text, DEFAULT_PIPELINE)]
        assert set(response.json()) == {"id", "final_abstract", "minutes", "next_actions"}


def test_invalid_material_and_keyword_placeholder_do_not_call_model():
    model = RecordingModel()
    with TestClient(create_app(settings(), model=model, frontend=False)) as client:
        response = client.post("/v1/fulltext/generate", json={"id": "short", "text": "会议开始。"})
        assert response.status_code == 200
        assert response.json()["message"] == response.json()["minutes"]["markdown"]
        body = client.post(
            "/v1/fulltext/generate", json={"id": "keyword", "text": TEXT, "pipeline": ["keywords"]}
        ).json()
        assert body == {"id": "keyword", "keywords": None}
        assert not model.calls


def test_one_capability_failure_is_safe_and_does_not_stop_later_capability():
    model = RecordingModel(fail="fulltext_abstract")
    with TestClient(create_app(settings(), model=model, frontend=False)) as client:
        response = client.post(
            "/v1/fulltext/generate",
            json={"id": "partial", "text": TEXT, "pipeline": ["abstract", "polish"]},
        )
        body = response.json()
        assert response.status_code == 200
        assert body["message"] == body["final_abstract"]
        assert body["polished_text"].startswith("[模拟]")
        assert "secret" not in response.text


async def test_polish_reads_every_character_across_budget_windows():
    model = RecordingModel()
    service = FulltextService(settings(cache_entries=0), model)
    text = TEXT * 20 + "最后一段不能丢失。"
    result = await service.run("long", text, ("polish",))
    assert "".join(payload["text"] for _, payload in model.calls) == text
    assert "最后一段不能丢失" in result["polished_text"]
    assert not service.tasks
    await service.close()


async def test_fulltext_queue_limit_timeout_cancellation_and_close(monkeypatch):
    entered, release, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(self, *args):
        entered.set()
        try:
            await release.wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(MeetingEngine, "generate_fulltext", blocked)
    service = FulltextService(
        settings(fulltext_max_active=1, fulltext_max_waiting=1, fulltext_queue_timeout=0.05),
        MockClient(),
    )
    first = asyncio.create_task(service.run("one", TEXT, DEFAULT_PIPELINE))
    await entered.wait()
    second = asyncio.create_task(service.run("two", TEXT, DEFAULT_PIPELINE))
    await asyncio.sleep(0)
    with pytest.raises(FulltextBusy, match="繁忙"):
        await service.run("three", TEXT, DEFAULT_PIPELINE)
    with pytest.raises(FulltextBusy, match="超时"):
        await second
    await service.close()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert cancelled.is_set()
    assert not service.tasks
    with pytest.raises(FulltextBusy, match="关闭"):
        await service.run("four", TEXT, DEFAULT_PIPELINE)


async def test_disconnect_cancels_engine_and_frees_capacity(monkeypatch):
    entered, cancelled, disconnect = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def blocked(self, *args):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def disconnected():
        return disconnect.is_set()

    monkeypatch.setattr(MeetingEngine, "generate_fulltext", blocked)
    service = FulltextService(settings(fulltext_max_active=1), MockClient())
    task = asyncio.create_task(service.run("one", TEXT, DEFAULT_PIPELINE, disconnected))
    await entered.wait()
    disconnect.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 1)
    assert cancelled.is_set()
    assert not service.tasks
    assert not service.capacity.locked()
    await service.close()


async def test_busy_http_response_preserves_selected_output_fields(monkeypatch):
    async def busy(*args, **kwargs):
        raise FulltextBusy("服务繁忙，请稍后重试。")

    app = create_app(settings(), frontend=False)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.fulltext, "run", busy)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/fulltext/generate",
                json={"id": "busy", "text": TEXT, "pipeline": ["minutes", "keywords"]},
            )
        assert response.status_code == 429
        body = response.json()
        assert set(body) == {"id", "message", "minutes", "keywords"}
        assert body["message"] == body["minutes"]["markdown"]
        assert not app.state.fulltext.tasks


async def test_summary_merges_all_windows_without_truncating_source():
    class Summarizer(RecordingModel):
        async def generate(self, task, instructions, context, schema):
            self.calls.append((task, context))
            return schema.model_validate({"text": "完整会议摘要。" * 30})

    model = Summarizer()
    service = FulltextService(settings(cache_entries=0), model)
    text = TEXT * 30 + "原文最后的结论。"
    result = await service.run("summary", text, ("abstract",))
    sources = [p["text"] for _, p in model.calls if p.get("phase") == "source"]
    assert "".join(sources) == text
    assert any(p.get("phase") == "merge" for _, p in model.calls)
    assert not result.get("message")
    assert result["final_abstract"]
    await service.close()


@pytest.mark.parametrize("recover", [False, True])
async def test_clipped_product_is_repaired_or_fails_without_publishing_fragment(recover):
    class ClippedModel(RecordingModel):
        async def generate(self, task, instructions, context, schema):
            self.calls.append((task, context))
            if task == "fulltext_actions":
                if recover and context.get("validation_error"):
                    return schema.model_validate({"text": "## 后续安排\n陈宁周五提交接口联调结果。"})
                limit = schema.model_json_schema()["properties"]["text"]["maxLength"]
                return schema.model_validate({"text": "完整安排。" + "截" * (limit - len("完整安排。"))})
            return await MockClient.generate(self, task, instructions, context, schema)

    model = ClippedModel()
    service = FulltextService(settings(), model)
    try:
        result = await service.run("clipped", TEXT, ("actions",))
        attempts = [payload for task, payload in model.calls if task == "fulltext_actions"]
        assert len(attempts) == 2
        assert all(payload["text"] == TEXT for payload in attempts)
        assert attempts[1]["validation_error"] == "product_incomplete_at_character_limit"
        if recover:
            assert not result.get("message")
            assert "陈宁周五提交" in result["next_actions"]["markdown"]
        else:
            assert result["message"] == result["next_actions"]["markdown"]
            assert not any(task == "fulltext_evidence" for task, _ in model.calls)
        assert "截" not in result["next_actions"]["markdown"]
    finally:
        await service.close()


async def test_minutes_splits_large_input_and_rejects_incomplete_consumption(monkeypatch):
    from types import SimpleNamespace

    received = []

    async def incomplete(self, chunks, **options):
        async for chunk in chunks:
            received.append(chunk)
        self.knowledge.draft = SimpleNamespace(markdown="不能发布的不完整纪要")
        self.stream.consumed = len(TEXT)

    monkeypatch.setattr(MeetingEngine, "run", incomplete)
    service = FulltextService(settings(), MockClient())
    text = TEXT * 200 + "尾部必须处理。"
    result = await service.run("long-minutes", text, ("minutes", "polish"))
    assert "".join(c.text for c in received) == text
    assert len(received) > 1
    assert all(len(c.text) <= 16000 for c in received)
    assert [c.seq for c in received] == list(range(1, len(received) + 1))
    assert received[-1].end == len(text)
    assert result["message"] == result["minutes"]["markdown"]
    assert "不完整纪要" not in result["minutes"]["markdown"]
    assert "尾部必须处理" in result["polished_text"]
    await service.close()


@pytest.mark.parametrize("fail", [None, "minutes"])
async def test_fulltext_composes_once_after_consuming_every_source(fail):
    from meetnote.engine.context import Context

    model = RecordingModel(fail=fail)
    events = []
    context = Context(
        settings(batch_tokens=128, source_tokens=64, minutes_interval=0),
        model,
        lambda kind, payload: events.append((kind, payload)),
    )
    engine = MeetingEngine(context)
    text = TEXT * 8 + "最后确认由王敏周五提交风险清单。"
    try:
        result = await engine.generate_fulltext("final-only", text, ("minutes",))
        assert engine.stream.consumed == engine.stream.cursor == len(text)
        assert not engine.stream.pending
        assert len([task for task, _ in model.calls if task == "extract"]) > 1
        assert len([task for task, _ in model.calls if task == "minutes"]) == 1
        updates = [payload for kind, payload in events if kind == "minutes.updated"]
        if fail:
            assert result["message"] == result["minutes"]["markdown"]
            assert not updates
            assert not engine.finalized
        else:
            assert not result.get("message")
            assert len(updates) == 1
            assert updates[0]["covered_chars"] == len(text)
            assert engine.knowledge.draft.coverage["consumed_chars"] == len(text)
            assert engine.finalized
    finally:
        await model.close()


async def test_request_level_failure_returns_safe_500_and_selected_fields(monkeypatch):
    async def crash(*args, **kwargs):
        raise RuntimeError("private URL and API key")

    app = create_app(settings(), frontend=False)
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.fulltext, "run", crash)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/v1/fulltext/generate",
                json={"id": "crash", "text": TEXT, "pipeline": ["abstract", "actions"]},
            )
        assert response.status_code == 500
        body = response.json()
        assert set(body) == {"id", "message", "final_abstract", "next_actions"}
        assert body["message"] == body["next_actions"]["markdown"]
        assert "private" not in response.text
