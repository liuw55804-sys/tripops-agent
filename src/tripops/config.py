from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="TRIPOPS_",
        extra="ignore",
    )

    environment: Literal["local", "test", "staging", "production"] = "local"
    log_level: str = "INFO"
    agent_mode: Literal["offline", "llm"] = "offline"

    model_provider: Literal["openai_compatible"] = "openai_compatible"
    model_name: str = "qwen-plus"
    model_api_key: str = Field(default="", repr=False)
    model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    live_research_enabled: bool = True
    tavily_api_key: str = Field(default="", repr=False)
    external_http_timeout_seconds: float = Field(default=15, gt=0, le=60)
    external_user_agent: str = "TripOpsAgent/0.1 (+https://github.com/liuw55804-sys/tripops-agent)"

    checkpoint_db: Path = Path(".tripops/checkpoints.sqlite")
    artifact_dir: Path = Path(".tripops/artifacts")

    max_tool_calls: int = Field(default=24, ge=1, le=100)
    run_timeout_seconds: float = Field(default=180, gt=0, le=1800)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
