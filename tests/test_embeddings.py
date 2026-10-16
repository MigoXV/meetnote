import asyncio
from array import array

import grpc
import pytest

from meetnote.config import Settings
from meetnote.domain.results import Fact
from meetnote.domain.transcripts import TranscriptChunk
from meetnote.embeddings.client import GrpcEncoder, Vectors, recall
from meetnote.embeddings.evidence import source_windows
from meetnote.embeddings.proto import embedding_pb2 as pb
from meetnote.embeddings.proto import embedding_pb2_grpc as rpc
from meetnote.engine.context import Context
from meetnote.engine.runtime import MeetingEngine
from meetnote.llm.client import ModelFailure
from meetnote.llm.mock import MockClient


def config(**kwargs):
    return Settings(_env_file=None, provider="mock", **kwargs)


class Encoder:
    def __init__(self):
        self.calls = []
        self.model_id = "test-model"
        self.fail = False

    async def encode(self, texts):
        self.calls.append(texts)
        if self.fail:
            raise ModelFailure("embedding_rpc_unavailable")
        return self.model_id, [array("f", [1, 0]) for _ in texts], 0


def context(encoder=None, **kwargs):
    value = Context(config(**kwargs), MockClient(), lambda *_: None)
    value.vectors = Vectors(value.settings, value.stats, value.emit, encoder or Encoder())
    return value


async def test_recall_caches_and_preserves_duplicate_input_order_with_small_lru():
    encoder = Encoder()
    ctx = context(encoder, embedding_cache_entries=1)
    output = await ctx.vectors.encode(["a", "b", "a"])
    assert output[0] == output[2]
    assert encoder.calls == [["a", "b"]]
    assert len(ctx.vectors.cache) == 1
    assert ctx.vectors.cache_bytes <= ctx.settings.embedding_cache_bytes
    await ctx.vectors.encode(["b"])
    assert len(encoder.calls) == 1
    matches = await recall(ctx.vectors, ["q"], ["b", "c"], 1, 0.5)
    assert matches == [[(0, 1.0)]]


async def test_model_change_cannot_mix_cached_vectors():
    encoder = Encoder()
    ctx = context(encoder)
    await ctx.vectors.encode(["old"])
    encoder.model_id = "new-model"
    with pytest.raises(ModelFailure, match="model_changed"):
        await ctx.vectors.encode(["old", "new"])


async def test_semantic_recall_enables_paraphrase_but_does_not_commit_revision():
    ctx = context()
    runtime = MeetingEngine(ctx)
    old = Fact(
        id="f000001",
        text="服务器预定周五交货。",
        subject="服务器到货",
        topic="交付",
        kind="action",
        status="confirmed",
        source_ids=["s1.0"],
    )
    new = Fact(
        id="f000002",
        text="更正：计算设备正式改为下周一送达。",
        subject="计算设备配送",
        topic="采购",
        kind="action",
        status="confirmed",
        source_ids=["s2.0"],
    )
    for seq, fact in enumerate([old, new], 1):
        start = runtime.stream.cursor
        runtime.ingest(
            TranscriptChunk(
                chunk_id=str(seq), seq=seq, text=fact.text, start=start, end=start + len(fact.text)
            )
        )
    runtime.knowledge.facts = [old]
    calls = []
    original = ctx.model.generate

    async def generate(task, instructions, payload, schema):
        if task == "reconcile":
            calls.append(payload)
        return await original(task, instructions, payload, schema)

    ctx.model.generate = generate
    assert not runtime.candidates([new])
    assert await runtime.reconcile([new]) == []
    assert calls[0]["allowed_pairs"] == [[new.id, old.id]]
    assert runtime.knowledge.facts == [old] and old.status == "confirmed"


async def test_embedding_failure_and_cancel_do_not_advance_source_boundary():
    encoder = Encoder()
    ctx = context(encoder)
    runtime = MeetingEngine(ctx)
    encoder.fail = True
    text = "最终确认设备改为周四交货。"
    runtime.ingest(TranscriptChunk(chunk_id="1", seq=1, text=text, start=0, end=len(text)))
    with pytest.raises(ModelFailure):
        await runtime.cycle("flush")
    assert runtime.stream.consumed == 0 and not runtime.knowledge.facts
    started = asyncio.Event()

    async def waiting(texts):
        started.set()
        await asyncio.Event().wait()

    encoder.encode = waiting
    job = asyncio.create_task(runtime.cycle("flush"))
    await started.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    assert runtime.stream.consumed == 0 and not runtime.knowledge.facts


async def test_fulltext_summary_and_actions_retrieve_original_evidence():
    ctx = context()
    requests = []
    original = ctx.model.generate

    async def record(task, instructions, payload, schema):
        if task == "fulltext_evidence":
            requests.append(payload)
        return await original(task, instructions, payload, schema)

    ctx.model.generate = record
    text = "陈宁负责在周五完成接口联调。预算上限三万元，不能提前采购设备。" * 10
    result = await MeetingEngine(ctx).generate_fulltext("test", text, ["abstract", "actions"])
    assert "final_abstract" in result and "next_actions" in result
    assert len(requests) >= 2
    assert {request["capability"] for request in requests} == {"abstract", "actions"}
    for request in requests:
        for source in request["sources"]:
            assert source["text"] == text[source["start"] : source["end"]]
    assert ctx.stats["embedding_requests"] >= 1
    windows = source_windows(text)
    assert "".join(window["text"] for window in windows) == text


async def test_grpc_client_validates_response_and_closes_on_failure():
    class Service(rpc.EmbeddingServiceServicer):
        async def Encode(self, request, ctx):
            if request.texts[0] == "error":
                await ctx.abort(grpc.StatusCode.UNAVAILABLE, "internal secret")
            row = [1.0] + [0.0] * 1023
            if request.texts[0] == "nan":
                row[0] = float("nan")
            return pb.EncodeResponse(model_id="test", embeddings=[pb.Embedding(values=row)])

    server = grpc.aio.server()
    rpc.add_EmbeddingServiceServicer_to_server(Service(), server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    encoder = GrpcEncoder(config(embedding_target=f"127.0.0.1:{port}"))
    try:
        _, rows, _ = await encoder.encode(["valid"])
        assert len(rows[0]) == 1024
        with pytest.raises(ModelFailure, match="vector_invalid"):
            await encoder.encode(["nan"])
        with pytest.raises(ModelFailure, match="embedding_rpc_unavailable"):
            await encoder.encode(["error"])
        await encoder.encode(["valid"])
    finally:
        await server.stop(0)


def test_readiness_reports_embedding_failure_without_leaking_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from meetnote.web.factory import create_app

    async def fail(self):
        raise ModelFailure("embedding_rpc_unavailable")

    monkeypatch.setattr(GrpcEncoder, "ready", fail)
    app = create_app(Settings(_env_file=None), model=MockClient(), frontend=False)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        result = client.get("/health/ready")
        assert result.status_code == 503
        assert result.json() == {"status": "embedding_unavailable"}


def test_mock_readiness_does_not_require_vector_engine():
    from fastapi.testclient import TestClient

    from meetnote.web.factory import create_app

    with TestClient(create_app(config(), frontend=False)) as client:
        assert client.get("/health/ready").json() == {"status": "ok"}


async def test_fulltext_embedding_error_is_explicit_and_does_not_block_other_capability():
    from meetnote.engine.fulltext import FAILURE_MESSAGE

    encoder = Encoder()
    encoder.fail = True
    ctx = context(encoder)
    text = "陈宁负责周五交货，预算上限三万元。不能未经批准采购其他设备。"
    output = await MeetingEngine(ctx).generate_fulltext("test", text, ["abstract", "polish"])
    assert output["final_abstract"] == FAILURE_MESSAGE
    assert output["message"]
    assert text in output["polished_text"]


async def test_recall_filters_candidates_before_top_k_and_uses_fact_not_whole_turn():
    encoder = Encoder()
    ctx = context(encoder)
    rows = await recall(ctx.vectors, ["query"], ["self", "older"], 1, 0.5, allowed=[[1]])
    assert rows == [[(1, 1.0)]]
    # 大段来源可能包含多个无关事项，只用于后续核验，不进入事实查询。
    runtime = MeetingEngine(ctx)
    text = "确认设备周一交货。另一个事项是会议室周二预约，不能混为同一安排。"
    runtime.ingest(TranscriptChunk(chunk_id="1", seq=1, text=text, start=0, end=len(text)))
    fact = Fact(
        id="f000001",
        text="确认设备周一交货。",
        subject="设备交付",
        topic="采购",
        kind="action",
        status="confirmed",
        source_ids=["s1.0"],
    )
    await runtime.reconcile([fact])
    assert all(text not in value for call in encoder.calls for value in call)


async def test_paged_evidence_review_has_shared_output_budget_before_generation():
    from meetnote.embeddings.evidence import review_product

    ctx = context()
    calls = []

    async def expand(task, instructions, payload, schema):
        cap = schema.model_json_schema()["properties"]["text"]["maxLength"]
        calls.append((payload, cap))
        return schema.model_validate({"text": "审" * cap})

    ctx.model.generate = expand
    source = "各项工作需要核实，未批准的安排不能执行。" * 80
    product = "\n".join(f"任务{i:02}需要核实，不能视为已批准。" for i in range(70))
    result = await review_product(ctx, "actions", source, product)
    assert 1 < len(calls) < 70
    assert len(result) == 1800
    assert sum(cap for _, cap in calls) + len(calls) - 1 == 1800
    assert all(len(payload["evidence"]) > 1 for payload, _ in calls)
