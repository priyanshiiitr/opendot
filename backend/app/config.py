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
    agent_max_iterations: int = 6
    agent_max_tool_calls: int = 12
    agent_tool_timeout_seconds: float = 5.0
    agent_max_argument_bytes: int = 8_000
    agent_max_result_bytes: int = 24_000
    agent_default_timezone: str = "Asia/Calcutta"
    # Browser automation (Milestone 3). fixture_mode must stay false outside tests.
    browser_enabled: bool = True
    browser_headless: bool = True
    browser_navigation_timeout_ms: int = 15_000
    browser_tool_timeout_seconds: float = 30.0
    browser_max_actions: int = 20
    browser_max_extract_bytes: int = 20_000
    browser_artifact_dir: str = "./data/browser_artifacts"
    browser_fixture_mode: bool = False
    browser_fixture_host: str = "127.0.0.1"
    browser_fixture_port: int = 0
    browser_fixture_base_url: str = ""
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AI_TEAMMATES_", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
