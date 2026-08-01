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

    # Rate limit: public POST REQUESTS per IP per hour, counted whether or not the
    # request generates anything. `brief_rate_limit` alone leaves POST /api/brief
    # able to insert a `draft_squads` row per call forever (the row is written
    # before the cache is consulted) and leaves POST /api/signup, which sends a
    # welcome email, with no limit at all. Deliberately well above the generation
    # cap: this bounds row growth and mail volume, it is not the generation budget.
    request_rate_limit: int = 30
    request_rate_window_s: int = 3600

    # Failed /api/admin/* auth attempts per IP before the surface answers 429.
    # Only FAILURES are recorded, so a working operator never sees it. Generous on
    # purpose — a 32-byte token does not need a tight lockout, and locking the
    # operator out of their own dashboard is the more likely failure.
    admin_fail_limit: int = 30
    admin_fail_window_s: int = 900

    # FastAPI's /docs + /openapi.json. OFF by default: the schema enumerates every
    # admin route and its request shape, which is a free map for anyone probing the
    # public surface. Set DOCS_ENABLED=true locally when you want the explorer.
    docs_enabled: bool = False                          # DOCS_ENABLED


settings = ApiSettings()
