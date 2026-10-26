import json

import httpx
import pytest

from scripts.benchmark_prefix_cache import request, summarize


@pytest.mark.parametrize("terminal", ["response.completed", "response.incomplete"])
async def test_cache_benchmark_reads_actual_cached_tokens_and_length_terminal(terminal):
    events = [
        {"type": "response.output_text.delta", "delta": "合成输出"},
        {
            "type": terminal,
            "response": {
                "id": "test",
                "status": terminal.removeprefix("response."),
                "usage": {
                    "input_tokens": 8290,
                    "output_tokens": 64,
                    "input_tokens_details": {"cached_tokens": 6144},
                },
            },
        },
    ]
    body = "".join("data: " + json.dumps(e) + "\n\n" for e in events)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        result = await request(client, "http://model", "model", "合成输入", 64)
    assert result["cached_tokens"] == 6144
    assert result["input_tokens"] == 8290
    assert result["status"] == terminal.removeprefix("response.")
    assert result["total_ms"] >= result["ttft_ms"] >= 0


async def test_cache_benchmark_rejects_unconfirmed_stream():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200, text='data: {"type":"response.output_text.delta","delta":"片段"}\n\n'
            )
        )
    ) as client:
        with pytest.raises(RuntimeError, match="terminal response"):
            await request(client, "http://model", "model", "合成输入", 64)


def test_layout_summary_excludes_initial_uncached_revision():
    rows = [
        {
            "size": 8192,
            "phase": "stable_prefix",
            "revision": i,
            "ttft_ms": value,
            "total_ms": value + 1000,
            "input_tokens": 8290,
            "output_tokens": 64,
            "cached_tokens": 0 if i == 0 else 6144,
        }
        for i, value in enumerate([3000, 900, 1100])
    ]
    result = summarize(rows)[0]["phases"]["stable_prefix"]
    assert result["ttft_ms"] == 1000
    assert result["cached_tokens"] == 6144
