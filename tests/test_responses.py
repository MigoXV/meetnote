import asyncio
import json
from types import SimpleNamespace

import httpx
import openai
import pytest
from pydantic import BaseModel

from meetnote.config import Settings
from meetnote.llm.client import ModelFailure, ResponsesClient, strict_schema


class Answer(BaseModel):
    ok: bool


def sse(text: str, final="response.completed") -> str:
    events = [
        {
            "type": "response.output_text.delta",
            "delta": text,
            "sequence_number": 0,
            "item_id": "msg_1",
            "output_index": 0,
            "content_index": 0,
            "logprobs": [],
        },
        {
            "type": final,
            "sequence_number": 1,
            "response": {
                "id": "resp_1",
                "object": "response",
                "created_at": 0,
                "model": "spark25",
                "status": "completed" if final == "response.completed" else "incomplete",
                "output": [],
            },
        },
    ]
    return "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events)


async def test_responses_wire_contract_and_validation_correction():
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=sse('{"ok":true}' if len(requests) > 1 else '{"wrong":1}'),
        )

    sdk = openai.AsyncOpenAI(
        api_key="local",
        base_url="http://test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = ResponsesClient(Settings(_env_file=None), sdk)
    answer = await client.generate("probe", "Return JSON.", {"text": "输入"}, Answer)
    assert answer.ok
    assert len(requests) == 2
    assert requests[0]["store"] is False
    assert requests[0]["stream"] is True
    assert requests[0]["enable_thinking"] is False
    assert requests[0]["text"]["format"]["schema"] == strict_schema(Answer)
    assert "correction" in json.loads(requests[1]["input"])
    await client.close()


async def test_truncated_stream_is_not_success():
    sdk = openai.AsyncOpenAI(
        api_key="local",
        base_url="http://test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    text=sse('{"ok":true}', "response.incomplete"),
                )
            )
        ),
    )
    client = ResponsesClient(Settings(_env_file=None), sdk)
    with pytest.raises(ModelFailure, match="response.incomplete"):
        await client.generate("probe", "Return JSON.", {}, Answer)
    await client.close()


async def test_network_failure_retried_once():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"message": "unavailable"}})

    sdk = openai.AsyncOpenAI(
        api_key="local",
        base_url="http://test/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    client = ResponsesClient(Settings(_env_file=None), sdk)
    with pytest.raises(ModelFailure, match="model_unavailable"):
        await client.generate("probe", "Return JSON.", {}, Answer)
    assert calls == 2
    await client.close()


async def test_cancel_closes_stream():
    entered = asyncio.Event()
    closed = False

    class Stream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            entered.set()
            await asyncio.sleep(60)
            raise StopAsyncIteration

        async def close(self):
            nonlocal closed
            closed = True

    async def create(**kwargs):
        return Stream()

    transport = SimpleNamespace(responses=SimpleNamespace(create=create))
    client = ResponsesClient(Settings(_env_file=None), transport)
    task = asyncio.create_task(client.generate("probe", "JSON", {}, Answer))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed
