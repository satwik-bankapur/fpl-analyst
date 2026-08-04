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
    # JWT secret for full-view brief links (api/tokens.py). Minimum entropy is
    # enforced at startup, not here — see `tokens.assert_secret_usable`. Empty is
    # allowed at import time so Stage A tooling and the test suite still load.
    brief_token_secret: str = ""                        # BRIEF_TOKEN_SECRET
    brief_token_secret_prev: str = ""                   # BRIEF_TOKEN_SECRET_PREV

    # --- Stage B: billing (spec §1.6, §1.7, §4) ----------------------------
    # PADDLE_WEBHOOK_SECRET is the notification-destination secret, NOT the API key.
    # Empty => the webhook rejects everything (fail closed), same rule as ADMIN_TOKEN.
    paddle_webhook_secret: str = ""                     # PADDLE_WEBHOOK_SECRET
    paddle_api_key: str = ""                            # PADDLE_API_KEY
    paddle_api_base: str = "https://sandbox-api.paddle.com"   # PADDLE_API_BASE
    # THE price map. A client sends a plan NAME; the server looks the id up here.
    # A price id never round-trips through the browser, so it cannot be edited.
    paddle_price_monthly: str = ""                      # PADDLE_PRICE_MONTHLY
    paddle_price_season: str = ""                       # PADDLE_PRICE_SEASON
    # Calendar year the season pass expires in (30 June). One-time purchases carry
    # no period from Paddle, so the product defines it.
    season_pass_end_year: int = 2027                    # SEASON_PASS_END_YEAR
    # Reject a signed webhook whose timestamp is older than this. Paddle signs the
    # timestamp, so this is replay protection, not a clock-skew allowance.
    paddle_replay_window_s: int = 300                   # PADDLE_REPLAY_WINDOW_S

    # --- Stage B: the paywall flip (spec §6) -------------------------------
    # The ONE switch. `api.service.PAYWALL` reads this; gating itself stays in
    # `graph.sections.gate_sections` and nowhere else.
    paywall_enabled: bool = False                       # PAYWALL_ENABLED

    # Public base URL of the frontend, used to build links INSIDE emails. Distinct
    # from frontend_origin (a CORS allow-list entry) because a link in an email is
    # not a browser origin — keeping them separate means changing one cannot
    # silently change the other.
    public_web_url: str = "http://localhost:3000"       # PUBLIC_WEB_URL
    # Public base URL of THIS API. Only the RFC 8058 one-click List-Unsubscribe
    # target uses it: a mail client POSTs there unattended, so it must be a real
    # API origin — public_web_url is the Next.js app and serves no /api/* route.
    public_api_url: str = "http://127.0.0.1:8000"       # PUBLIC_API_URL

    # --- Stage B: the send batch (spec §3) ---------------------------------
    send_enabled: bool = False                          # SEND_ENABLED
    # Whole-batch ceiling. A runaway loop that mails the list twice is unrecoverable;
    # a batch that stops at the cap and says so is a Tuesday morning.
    send_max_recipients: int = 2000                     # SEND_MAX_RECIPIENTS
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

    # Whole-service brief generations per window, across every caller (audit §C2).
    # The per-IP limit stops one abuser; this caps the bill when the per-IP limit
    # has a bug. Sized well above real demand — it is a circuit breaker, not a quota.
    global_generation_limit: int = 500                  # GLOBAL_GENERATION_LIMIT
    global_generation_window_s: int = 3600


settings = ApiSettings()
