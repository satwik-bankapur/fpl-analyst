"""Runtime configuration for the data layer.

Only Layer-1 concerns live here (API base, User-Agent, DB URL, schedule timing).
The big versioned heuristic config object is Layer 2's and does NOT belong here.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FPL_", env_file=".env", extra="ignore")

    # FPL API. Default Python UA is occasionally 403'd — always send a browser-like one.
    api_base_url: str = "https://fantasy.premierleague.com/api/"
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
    http_timeout_s: float = 30.0
    http_max_retries: int = 3
    # Politeness: no published rate limit, so throttle batch work and back off on 429/403.
    http_backoff_base_s: float = 1.5

    # Database. SQLite is acceptable in dev; Postgres in prod (set FPL_DATABASE_URL).
    database_url: str = "sqlite:///fpl.db"

    # Timezone the schedule windows are expressed in (spec §3 uses IST).
    schedule_timezone: str = "Asia/Kolkata"


settings = Settings()
