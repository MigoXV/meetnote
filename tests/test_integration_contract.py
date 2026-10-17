import httpx

from meetnote.evaluation.integration import check_snapshot


def test_completed_snapshot_still_rejects_unconsumed_input():
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
        "stats": {"responses_requests": 1},
    }
    assert not check_snapshot(snapshot)
    snapshot["remaining_chars"] = 2
    assert set(check_snapshot(snapshot)) == {"input_not_fully_consumed"}


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
        "stats": {"responses_requests": 1},
    }

    async def handle(request):
        path = request.url.path
        if path.endswith("/info"):
            return httpx.Response(
                200,
                json={
                    "provider": "responses",
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
