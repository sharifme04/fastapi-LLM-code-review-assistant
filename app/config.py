"""Application configuration via pydantic-settings."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Application
    app_name: str = "code-review-assistant"
    app_version: str = "1.0.0"
    debug: bool = False

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/code_review"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Anthropic
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-20250514"

    # Cost Control
    daily_cost_limit: float = 10.00

    # Rate Limiting
    rate_limit: str = "20/minute"

    # Code Review
    max_code_lines: int = 500

    # Anthropic Pricing (per 1M tokens) — updated as of 2025
    # These can be overridden via environment variables
    anthropic_input_price_per_mtok: float = 3.00
    anthropic_output_price_per_mtok: float = 15.00


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()
