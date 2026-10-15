"""所有入口共用主模型；不再路由到独立决策服务。"""

from meetnote.llm.client import ResponsesClient
from meetnote.llm.mock import MockClient


def create_model(settings):
    return MockClient() if settings.provider == "mock" else ResponsesClient(settings)


async def model_info(settings):
    if settings.provider != "responses":
        raise ValueError("真实评估不能使用 mock 主模型")
    return {"model": settings.model, "base_url": settings.base_url, "provider": settings.provider}
