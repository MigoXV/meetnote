import httpx
import pytest

from meetnote.config import Settings
from meetnote.domain.results import field_support_schema, relation_schema
from meetnote.engine.context import Context
from meetnote.engine.decisions import DecisionRouter, DecisionTasks
from meetnote.llm.client import ModelFailure
from meetnote.llm.laya import LayaClient
from meetnote.llm.mock import MockClient


class Choices:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = []

    async def decide(self, task, state, questions, fingerprint):
        self.calls.append((task, state, questions))
        return {"answer": {"type": "choice", "choice": next(self.values)}}


async def test_fields_use_own_evidence_and_uncertainty_is_not_false():
    client = Choices(["supported", "uncertain"])
    adapter = DecisionTasks(client, "fixture")
    payload = {
        "claims": [
            {
                "id": "f1",
                "text": "测试",
                "owner": "小李",
                "owner_source_ids": ["s1"],
                "due": "周五",
                "due_source_ids": ["s2"],
            }
        ],
        "sources": [{"id": "s1", "text": "小李负责测试"}, {"id": "s2", "text": "周五再讨论"}],
    }
    with pytest.raises(ModelFailure, match="uncertain"):
        await adapter.fields(payload)
    assert client.calls[0][1]["原文"] == [payload["sources"][0]]
    assert client.calls[1][1]["原文"] == [payload["sources"][1]]


async def test_relation_uses_allowed_kinds_and_selects_evidence():
    client = Choices(["supersedes", "e0"])
    schema = relation_schema({"p0": ["s2#0"]}, {"p0": ["none", "supersedes"]})
    payload = {
        "pairs": {
            "p0": {
                "new_claim": "预算改为二十万",
                "old_claim": "预算十万",
                "new_status": "confirmed",
                "old_status": "confirmed",
                "evidence_ids": ["s2#0"],
            }
        },
        "sources": [{"id": "s2#0", "text": "预算正式改为二十万元。"}],
    }
    output = await DecisionTasks(client, "fixture").reconcile(payload, schema)
    assert schema.model_validate(output).p0.evidence_id == "s2#0"
    assert set(client.calls[0][2]["answer"]["criteria"]) == {"none", "supersedes"}


async def test_http_rejects_changed_model_or_unknown_choice():
    async def handle(request):
        return httpx.Response(200, json={"model": {"fingerprint": "wrong"}, "answers": {}})

    client = LayaClient("http://local", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(ModelFailure, match="model_changed"):
            await client.decide("fields", {}, {"a": {"criteria": ["yes", "no"]}}, "expected")
    finally:
        await client.close()


async def test_backend_fingerprint_invalidates_context_cache():
    class Client(Choices):
        revision = "a"

        async def info(self):
            return {"ready": True, "fingerprint": self.revision}

    client = Client(["supported", "unsupported"])
    model = DecisionRouter(MockClient(), client)
    settings = Settings(_env_file=None, provider="mock")
    ctx = Context(settings, model, lambda *_: None)
    payload = {
        "claims": [{"id": "f1", "text": "测试", "owner": "小李", "owner_source_ids": ["s1"]}],
        "sources": [{"id": "s1", "text": "小李负责测试"}],
    }
    schema = field_support_schema(["f1"])
    assert (await ctx.invoke("fields", payload, schema)).f1.owner_supported
    assert (await ctx.invoke("fields", payload, schema)).f1.owner_supported
    assert len(client.calls) == 1
    client.revision = "b"
    assert not (await ctx.invoke("fields", payload, schema)).f1.owner_supported
    assert len(client.calls) == 2


async def test_opt_in_fallback_is_visible():
    class Client:
        async def info(self):
            raise ModelFailure("unavailable")

    events = []
    ctx = Context(
        Settings(_env_file=None, provider="mock"),
        DecisionRouter(MockClient(), Client(), True),
        lambda k, d: events.append((k, d)),
    )
    payload = {"claims": [{"id": "f1", "text": "测试", "owner": "", "due": ""}], "sources": []}
    await ctx.invoke("fields", payload, field_support_schema(["f1"]))
    assert ctx.stats["decision_fallbacks"] == 1
    assert any(k == "model.fallback" for k, _ in events)
