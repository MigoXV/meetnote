import httpx
import pytest

from meetnote.domain.results import relation_schema
from meetnote.engine.decisions import DecisionTasks
from meetnote.evaluation.integration import check_snapshot
from meetnote.llm.client import ModelFailure
from meetnote.llm.laya import LayaClient


class Client:
    def __init__(self, choices):
        self.choices = iter(choices)
        self.calls = []

    async def decide(self, task, state, questions, fingerprint):
        self.calls.append((state, questions))
        return {"answer": {"choice": next(self.choices)}}


async def test_pair_scope_and_evidence_limit():
    sources = [{"id": f"s1#{i}", "source_id": "s1", "text": "明确修改"} for i in range(10)]
    payload = {
        "new": [{"id": "n", "source_ids": ["s1"]}],
        "candidates": [{"id": "o", "source_ids": ["s0"]}],
        "pairs": {
            "p0": {
                "new_id": "n",
                "old_id": "o",
                "new_claim": "新安排",
                "old_claim": "旧安排",
                "new_status": "confirmed",
                "old_status": "confirmed",
                "evidence_ids": [s["id"] for s in sources],
            }
        },
        "sources": sources + [{"id": "other#0", "source_id": "other", "text": "无关事实"}],
    }
    schema = relation_schema({"p0": [s["id"] for s in sources]}, {"p0": ["none", "supersedes"]})
    client = Client(["supersedes"])
    with pytest.raises(ModelFailure, match="evidence_options_exceeded"):
        await DecisionTasks(client, "test").reconcile(payload, schema)
    assert len(client.calls) == 1
    assert all(s["source_id"] == "s1" for s in client.calls[0][0]["原文"])
    payload["pairs"]["p0"]["evidence_ids"].pop()
    client = Client(["supersedes", "e8"])
    result = await DecisionTasks(client, "test").reconcile(payload, schema)
    assert result["p0"]["evidence_id"] == "s1#8"
    assert len(client.calls[1][1]["answer"]["criteria"]) == 10


async def test_http_budget_failure_is_explicit():
    async def respond(request):
        return httpx.Response(413, text="complete question exceeds input token limit")

    client = LayaClient("http://local", transport=httpx.MockTransport(respond))
    try:
        with pytest.raises(ModelFailure, match="laya_http_413"):
            await client.decide(
                "fields", {}, {"answer": {"criteria": {"yes": "是", "no": "否"}}}, "id"
            )
    finally:
        await client.close()


def test_completed_snapshot_still_rejects_fallback_or_unconsumed_input():
    snapshot = {
        "status": "completed",
        "accepted_count": 1,
        "processed_count": 1,
        "remaining_chars": 0,
        "minutes": {"title": "test"},
        "facts": [{"source_ids": ["s1"]}],
        "sources": [{"id": "s1"}],
        "error": None,
        "warnings": [],
        "stats": {"responses_requests": 1, "laya_requests": 1},
    }
    assert not check_snapshot(snapshot)
    snapshot["stats"]["decision_fallbacks"] = 1
    snapshot["remaining_chars"] = 2
    assert set(check_snapshot(snapshot)) == {"unexpected_fallback", "input_not_fully_consumed"}


async def test_decision_failure_never_calls_main_model_when_fallback_disabled():
    from meetnote.engine.decisions import DecisionRouter

    class Unavailable:
        async def info(self):
            raise ModelFailure("laya_unavailable")

    class Main:
        async def generate(self, *args):
            pytest.fail("strict integration must not fall back")

    with pytest.raises(ModelFailure, match="laya_unavailable"):
        await DecisionRouter(Main(), Unavailable(), False).generate("fields", "", {}, None)


def test_web_startup_rejects_wrong_precision_and_closes_client(monkeypatch):
    from fastapi.testclient import TestClient

    from meetnote.config import Settings
    from meetnote.web.factory import create_app

    class Decision:
        async def info(self):
            return {"ready": True, "fingerprint": "test", "precision": "fp16", "backend": "vllm"}

    class Router:
        client = Decision()
        closed = False

        async def close(self):
            self.closed = True

    router = Router()
    monkeypatch.setattr("meetnote.web.factory.create_model", lambda settings: router)
    app = create_app(Settings(_env_file=None, decision_backend="laya"), frontend=False)
    with pytest.raises(RuntimeError, match="BF16"):
        with TestClient(app):
            pass
    assert router.closed


async def test_http_probe_reads_existing_sse_wire_format(monkeypatch):
    from meetnote.evaluation import integration
    from meetnote.sessions.events import Event

    deleted = []
    snapshot = {
        "status": "completed",
        "accepted_count": 1,
        "processed_count": 1,
        "remaining_chars": 0,
        "minutes": {"title": "test"},
        "facts": [{"source_ids": ["s1"]}],
        "sources": [{"id": "s1"}],
        "error": None,
        "warnings": [],
        "stats": {"responses_requests": 1, "laya_requests": 1},
    }

    async def handle(request):
        path = request.url.path
        if path.endswith("/info"):
            return httpx.Response(
                200,
                json={
                    "provider": "responses",
                    "decision_backend": "laya",
                    "decision_fallback": False,
                    "decision_model": {"precision": "bf16"},
                },
            )
        if "/samples/" in path:
            return httpx.Response(
                200,
                json={
                    "title": "test",
                    "meeting_date": "2026-10-02",
                    "segments": [{"id": "1", "text": "测试。"}],
                },
            )
        if path.endswith("/sessions"):
            return httpx.Response(201, json={"id": "test"})
        if path.endswith("/events"):
            return httpx.Response(200, text=Event(1, "session.snapshot", {}).wire())
        if request.method == "DELETE":
            deleted.append(path)
            return httpx.Response(204)
        if path.endswith("/test"):
            return httpx.Response(200, json=snapshot)
        return httpx.Response(200, json={})

    client_class = httpx.AsyncClient
    monkeypatch.setattr(
        integration.httpx,
        "AsyncClient",
        lambda **kw: client_class(**kw, transport=httpx.MockTransport(handle)),
    )
    reports = []
    assert await integration.run_http("http://local", reports.append) == 0
    assert "session.snapshot" in reports[0]
    assert deleted == ["/api/v1/sessions/test"]
