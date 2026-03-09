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

    # Public URL (tunnel or deployed domain, for serving images to external APIs)
    public_base_url: str | None = None

    # CORS
    cors_allowed_origins: str = "*"

    # Polling
    poll_interval_seconds: int = 10
    poll_max_wait_seconds: int = 3600

    # Provider-specific
    veo_model: str = "veo-3-generate-001"
    luma_camera_model: str = "normal"

    # Agent pipeline
    agent_max_rounds: int = 2              # 1 = compete only, 2 = compete + refine
    agent_strategy: str = "compete"        # "compete" | "luma_only" | "veo_only"
    cost_per_luma_generation: float = 0.71
    cost_per_veo_generation: float = 1.20

    # Stripe
    stripe_secret_key: str | None = None
    stripe_publishable_key: str | None = None
    stripe_webhook_secret: str | None = None

    # Email (Gmail SMTP)
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    notification_from: str = "orders@opendoorcinematic.com"

    # Package pricing
    package_prices: dict = {
        "essential": 79.0,
        "signature": 139.0,
        "premium": 199.0,
    }
    price_per_extra_room: dict = {
        "essential": 20.0,
        "signature": 30.0,
        "premium": 40.0,
    }

    # Add-on pricing
    addon_prices: dict = {
        "rush_delivery": 140.0,
        "extra_revision": 35.0,
        "custom_staging": 70.0,
        "instagram_carousel": 35.0,
        "unique_request": 0.0,  # custom pricing, handled manually
    }


settings = Settings()



