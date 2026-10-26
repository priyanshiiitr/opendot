from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Teammates"
    database_url: str = "sqlite:///./data/ai_teammates.db"
    model_provider: str = "mock"
    xai_api_key: str | None = None
    xai_model: str = "grok-4.6"
    xai_base_url: str = "https://api.x.ai/v1"
    # Groq Cloud (console.groq.com) — free tier available; OpenAI-compatible API.
    groq_api_key: str | None = None
    groq_model: str = "openai/gpt-oss-20b"
    groq_base_url: str = "https://api.groq.com/openai/v1"
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
    # Milestone 5 — durable automations (local SQLite queue + lifespan worker).
    automation_worker_enabled: bool = True
    automation_worker_poll_seconds: float = 1.0
    automation_scheduler_tick_seconds: float = 15.0
    automation_max_queued: int = 50
    automation_lease_seconds: float = 120.0
    automation_default_timezone: str = "Asia/Calcutta"
    webhook_max_body_bytes: int = 65_536
    webhook_rate_limit_per_minute: int = 30
    # Milestone 6 — per-teammate computers (persistent browser + workspace under app data).
    computers_root_dir: str = ""  # empty → %LOCALAPPDATA%/AITeammates/computers (or ./data/computers)
    computer_preview_max_width: int = 1024
    computer_preview_min_interval_ms: int = 1500
    computer_max_file_bytes: int = 256_000
    computer_headless_default: bool = True
    # Milestone 10 — provider: local_browser | isolated_linux_dev | gcp_compute | cloud_sandbox
    computer_default_provider: str = "local_browser"
    # Paid out-of-pocket path — must stay false unless separately authorized.
    computer_allow_billable: bool = False
    # Credit-funded GCP demo path (free trial / promotional credits). Separate from billable.
    computer_credit_funded_enabled: bool = False
    # Daytona managed sandbox (free/credit wallet). Default off until key + explicit enable.
    computer_cloud_sandbox_enabled: bool = False
    computer_idle_stop_minutes: int = 30
    computer_max_runtime_minutes: int = 240
    # Daytona — prefer DAYTONA_API_KEY in .env; AI_TEAMMATES_DAYTONA_API_KEY also works. Never commit.
    daytona_api_key: str = ""
    daytona_api_url: str = ""  # default SDK: https://app.daytona.io/api
    daytona_target: str = ""  # optional region
    daytona_max_concurrent_sandboxes: int = 1
    # Google Compute Engine (credentials via ADC or credentials file path — never commit secrets)
    gcp_project_id: str = ""
    gcp_zone: str = "us-central1-a"
    gcp_machine_type: str = "e2-micro"
    gcp_allowed_machine_types: str = "e2-micro,e2-small"
    gcp_boot_disk_gb: int = 20
    gcp_max_concurrent_vms: int = 1
    gcp_control_plane_url: str = ""  # https://… always-on API the guest agent pulls from
    gcp_credentials_file: str = ""  # optional path to SA JSON; prefer ADC / secret mount
    # Durable execution backend: in_process (default) | external_stub | trigger_http
    durable_execution_backend: str = "in_process"
    durable_external_backend_enabled: bool = False
    # Trigger.dev-style HTTP enqueue (only when backend=trigger_http + external enabled)
    trigger_webhook_url: str = ""
    trigger_webhook_secret: str = ""
    # Optional Clerk auth (off by default). Local pairing remains primary.
    clerk_enabled: bool = False
    clerk_jwks_url: str = ""  # e.g. https://<clerk-domain>/.well-known/jwks.json
    clerk_issuer: str = ""  # e.g. https://<clerk-domain>
    clerk_audience: str = ""
    # Optional OAuth connectors (Gmail/GitHub) — refuse until enabled + client id/secret set
    oauth_connectors_enabled: bool = False
    oauth_redirect_base: str = "http://127.0.0.1:8000"
    github_oauth_client_id: str = ""
    github_oauth_client_secret: str = ""
    gmail_oauth_client_id: str = ""
    gmail_oauth_client_secret: str = ""
    # Optional: Trigger task callback target (usually same as this API)
    control_plane_callback_url: str = ""
    # Optional Postgres: set AI_TEAMMATES_DATABASE_URL=postgresql+psycopg://... (SQLite remains default).
    # Do not auto-migrate production data without an explicit approved plan.
    # Milestone 7 — memory / skills / workflows
    skills_root_dir: str = "./data/skills"
    memory_max_retrieve: int = 12
    # Milestone 8 — multi-agent teams (bounded coordinator)
    team_max_subtasks: int = 12
    team_max_concurrent: int = 2
    team_max_depth: int = 1
    team_max_retries: int = 1
    team_max_total_runs: int = 20
    team_shared_artifacts_root: str = "./data/team_artifacts"
    # Milestone 9 — always-on service + optional remote companion (disabled by default)
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    remote_access_enabled: bool = False
    local_trust_loopback: bool = True  # desktop/Electron on 127.0.0.1 skips pairing
    pairing_code_ttl_seconds: int = 300
    session_ttl_hours: int = 168  # 7 days
    pairing_rate_limit_per_minute: int = 10
    auth_rate_limit_per_minute: int = 60
    notify_on_completion: bool = True
    notify_on_failure: bool = True
    notify_on_approval: bool = True
    notify_on_reconciliation: bool = True
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AI_TEAMMATES_", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
