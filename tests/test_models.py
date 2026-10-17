import asyncio

from fastapi.testclient import TestClient

from meetnote.config import Settings
from meetnote.llm.client import ResponsesClient
from meetnote.web.factory import create_app


def test_only_main_model_is_constructed_even_with_old_environment(monkeypatch):
    from meetnote.engine.models import create_model

    monkeypatch.setenv("MEETNOTE_DECISION_BACKEND", "laya")
    monkeypatch.setenv("MEETNOTE_LAYA_URL", "http://must-never-be-contacted")
    config = Settings(_env_file=None)
    assert not {"decision_backend", "laya_url", "laya_timeout", "laya_fallback"} & Settings.model_fields.keys()
    model = create_model(config)
    assert isinstance(model, ResponsesClient)
    asyncio.run(model.close())


def test_info_has_no_decision_service_contract():
    with TestClient(create_app(Settings(_env_file=None, provider="mock"), frontend=False)) as client:
        info = client.get("/api/v1/info").json()
        assert info["model"] == "spark25"
        assert not any(key.startswith("decision_") for key in info)
