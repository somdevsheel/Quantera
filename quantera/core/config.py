"""Platform settings from QUANTERA_* environment variables (and the gitignored .env file).

Required: QUANTERA_DATABASE_URL, QUANTERA_JWT_SECRET (>= 32 characters).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUANTERA_", env_file=ROOT / ".env",
                                      extra="ignore")

    environment: Literal["development", "test", "production"] = "development"
    database_url: str
    db_schema: str | None = None  # tests use a throwaway schema
    jwt_secret: str = Field(min_length=32)
    # Encrypts MFA secrets at rest. Defaults to the JWT secret; set it separately so the JWT
    # secret can be rotated without resetting everyone's authenticator.
    mfa_key: str | None = Field(default=None, min_length=32)
    password_reset_ttl_seconds: int = 1800
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 14 * 24 * 3600
    password_min_length: int = 12
    auth_rate_limit_per_minute: int = 20
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    lake_api_url: str = "http://127.0.0.1:8001"
    lake_release: str | None = None
    ml_four_eyes: bool = True  # a model is approved by someone other than its trainer
    enable_live_trading: Literal[False] = False  # paper only; there is no live execution path

    @field_validator("database_url")
    @classmethod
    def _postgres_only(cls, v: str) -> str:
        if not v.startswith("postgresql"):
            raise ValueError("the platform database must be PostgreSQL")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()  # values come from the environment / .env
