"""入口共享模型组合根，避免服务与评估配置分叉。"""

from meetnote.engine.decisions import DecisionRouter
from meetnote.llm.client import ResponsesClient
from meetnote.llm.laya import LayaClient
from meetnote.llm.mock import MockClient


def create_model(settings):
    base = MockClient() if settings.provider == "mock" else ResponsesClient(settings)
    if settings.decision_backend == "laya":
        return DecisionRouter(
            base, LayaClient(settings.laya_url, settings.laya_timeout), settings.laya_fallback
        )
    return base
