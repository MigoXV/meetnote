"""进程配置只在组合根读取，业务代码接收明确的配置对象。"""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEETNOTE_", env_file=".env", extra="ignore")

    provider: Literal["responses", "mock"] = "responses"
    base_url: str = "http://192.168.0.222:10001/v1"
    model: str = "spark25"
    api_key: str = Field(default="local", repr=False)
    thinking: bool = False
    temperature: float = Field(default=0, ge=0, le=2)
    model_timeout: float = Field(default=240, gt=0)
    max_output_tokens: int = Field(default=4096, ge=64, le=32768)
    model_concurrency: int = Field(default=2, ge=1)
    tokenizer_path: Path = Path("/workspace/model-bin/XHToken/Spark-X2.5-4B-INT8/tokenizer.json")
    body_max_items: int = Field(default=18, ge=6, le=60)
    batch_tokens: int = Field(default=768, ge=128, le=4096)
    batch_sources: int = Field(default=12, ge=1, le=32)
    source_tokens: int = Field(default=384, ge=32, le=1024)
    input_token_budget: int = Field(default=8192, ge=2048, le=32768)
    protocol_token_reserve: int = Field(default=1024, ge=256)
    context_tokens: int = Field(default=128000, ge=4096)
    cache_entries: int = Field(default=128, ge=0)
    cache_bytes: int = Field(default=8 * 1024 * 1024, ge=0)
    checkpoint_seconds: float = Field(default=9, ge=0)
    max_sessions: int = Field(default=32, ge=1)
    queue_size: int = Field(default=256, ge=1)
    max_session_chars: int = Field(default=200_000, ge=1)
    idle_seconds: float = Field(default=1800, gt=0)
    retention_seconds: float = Field(default=300, gt=0)
    event_count: int = Field(default=1024, ge=1)
    event_bytes: int = Field(default=4 * 1024 * 1024, ge=1024)
    corpus_dir: Path = Path("/workspace/model-bin/meetnote")

    @model_validator(mode="after")
    def check_budget(self) -> "Settings":
        if self.source_tokens > self.batch_tokens:
            raise ValueError("source_tokens 不能超过 batch_tokens")
        if (
            self.input_token_budget + self.max_output_tokens + self.protocol_token_reserve
            > self.context_tokens
        ):
            raise ValueError("请求预算超过模型上下文")
        return self
