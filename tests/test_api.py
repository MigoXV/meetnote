import json
import time

import pytest
from fastapi.testclient import TestClient

from meetnote.config import Settings
from meetnote.web.factory import create_app


def make_app(**kwargs):
    return create_app(Settings(_env_file=None, provider="mock"), **kwargs)


def complete(client):
    created = client.post("/api/v1/sessions", json={"title": "接口测试"}).json()
    session_id = created["id"]
    assert (
        client.post(
            f"/api/v1/sessions/{session_id}/chunks",
            json={"text": "陈宁负责在周五完成验收。", "chunk_id": "one"},
        ).status_code
        == 202
    )
    assert client.post(f"/api/v1/sessions/{session_id}/finalize").status_code == 202
    for _ in range(100):
        snapshot = client.get(f"/api/v1/sessions/{session_id}").json()
        if snapshot["status"] == "completed":
            return session_id, snapshot
        time.sleep(0.01)
    pytest.fail("mock session did not finish")


def test_late_sse_subscriber_gets_final_snapshot_and_resume():
    with TestClient(make_app(frontend=False)) as client:
        session_id, snapshot = complete(client)
        response = client.get(f"/api/v1/sessions/{session_id}/events")
        records = [
            json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")
        ]
        assert records[-1]["data"]["status"] == "completed"
        assert records[-1]["data"]["minutes"]
        resumed = client.get(
            f"/api/v1/sessions/{session_id}/events", headers={"Last-Event-ID": "0"}
        )
        assert '"type": "summary.committed"' in resumed.text
        assert (
            client.get(
                f"/api/v1/sessions/{session_id}/events", headers={"Last-Event-ID": "oops"}
            ).status_code
            == 400
        )
        assert snapshot["remaining_chars"] == 0


def test_api_validation_and_delete():
    with TestClient(make_app(frontend=False)) as client:
        sid = client.post("/api/v1/sessions", json={}).json()["id"]
        assert (
            client.post(f"/api/v1/sessions/{sid}/chunks", json={"text": "   "}).status_code == 422
        )
        assert (
            client.post(
                f"/api/v1/sessions/{sid}/chunks", json={"text": "a", "interim": True}
            ).status_code
            == 422
        )
        assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204
        assert client.get(f"/api/v1/sessions/{sid}").status_code == 404


def test_frontend_routes_and_api_fallback(tmp_path):
    (tmp_path / "index.html").write_text("<html>MeetNote workspace</html>")
    (tmp_path / "asset.js").write_text("window.MEETNOTE = true")
    with TestClient(make_app(dist=tmp_path)) as client:
        assert "MeetNote workspace" in client.get("/").text
        assert (
            "MeetNote workspace"
            in client.get("/meetings/example", headers={"Accept": "text/html"}).text
        )
        assert "window.MEETNOTE" in client.get("/asset.js").text
        assert client.get("/openapi.json").status_code == 200
        for accept in ("application/json", "text/html"):
            assert client.get("/api/unknown", headers={"Accept": accept}).status_code == 404
        assert client.head("/api/unknown").status_code == 404
    with pytest.raises(RuntimeError, match="前端构建缺失"):
        make_app(dist=tmp_path / "missing")
