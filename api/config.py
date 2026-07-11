"""Delivery-layer runtime settings (spec §5 env vars).

Kept separate from `fpl.config` (Layer 1, FPL_-prefixed) on purpose: these are the
public-surface secrets/knobs, read under the spec's bare env names. Note the one
mapping the spec leaves implicit: spec `DATABASE_URL` is Layer 1's existing
`FPL_DATABASE_URL` (fpl/config.py) — the DB engine is owned there, not here.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # CORS: the single frontend origin allowed to call the API.
    frontend_origin: str = "http://localhost:3000"      # FRONTEND_ORIGIN
    # Bearer token for the admin curated-table endpoints (§1.9).
    admin_token: str = ""                               # ADMIN_TOKEN
    # Resend transactional email. Empty key => mail is a logged no-op (dev/tests).
    resend_api_key: str = ""                            # RESEND_API_KEY
    send_from: str = "FPL Analyst <briefs@example.com>"  # SEND_FROM
    # JWT secret for full-view brief links. Unused in Stage A (no tokens minted).
    brief_token_secret: str = ""                        # BRIEF_TOKEN_SECRET
    # In-process APScheduler gate. OFF by default so tests / local demo never spawn
    # it; Railway sets RUN_SCHEDULER=true (single replica — see railway.toml).
    run_scheduler: bool = False                         # RUN_SCHEDULER

    # Rate limit: brief GENERATIONS per IP per hour (cache hits are exempt, §1.1).
    brief_rate_limit: int = 5
    brief_rate_window_s: int = 3600


settings = ApiSettings()
