from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "env.local", "env.example"),
        env_prefix="",
        extra="ignore",
    )

    mcp_data_dir: str = "./data"
    mcp_inbox_dir: str = "./data/inbox"
    mcp_db_path: str = "./data/mcp.sqlite3"
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8000

    luma_api_key: str | None = None
    luma_api_base_url: str | None = None

    veo_api_key: str | None = None
    veo_api_base_url: str | None = None

    monthly_budget_usd: float = 500.0

    # S3 / Cloudflare R2
    s3_endpoint_url: str | None = None
    s3_access_key_id: str | None = None
    s3_secret_access_key: str | None = None
    s3_bucket_name: str = "real-render"
    s3_region: str = "auto"
    s3_presigned_url_expiry: int = 3600

    # CORS
    cors_allowed_origins: str = "*"

    # Polling
    poll_interval_seconds: int = 10
    poll_max_wait_seconds: int = 3600

    # Provider-specific
    veo_model: str = "veo-3-generate-001"
    luma_camera_model: str = "normal"


settings = Settings()



