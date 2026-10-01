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
    temperature: float = Field(default=0.1, ge=0, le=2)
    model_timeout: float = Field(default=240, gt=0)
    max_output_tokens: int = Field(default=4096, ge=64, le=32768)
    model_concurrency: int = Field(default=4, ge=1)
    min_chars: int = Field(default=50, ge=1)
    window_chars: int = Field(default=700, ge=1)
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
    def check_window(self) -> "Settings":
        if self.min_chars > self.window_chars:
            raise ValueError("min_chars 不能超过 window_chars")
        return self
