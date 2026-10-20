from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    app_name: str = "AI Teammates"
    database_url: str = "sqlite:///./data/ai_teammates.db"
    model_provider: str = "mock"
    xai_api_key: str | None = None
    xai_model: str = "grok-4.6"
    xai_base_url: str = "https://api.x.ai/v1"
    cors_origins: str = "http://localhost:5173"
    agent_max_iterations: int = 4
    agent_max_tool_calls: int = 6
    agent_tool_timeout_seconds: float = 5.0
    agent_max_argument_bytes: int = 8_000
    agent_max_result_bytes: int = 12_000
    agent_default_timezone: str = "Asia/Calcutta"
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AI_TEAMMATES_", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

@lru_cache
def get_settings() -> Settings:
    return Settings()
