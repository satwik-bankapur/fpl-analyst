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

    # --- Who may generate a free brief (2026-08-06) ------------------------
    # THE CONVERSION KNOB. Three modes, enforced server-side in POST /api/brief:
    #
    #   open         no sign-in, ever. The design the accounts build was given,
    #                and the DEFAULT — everything else here is opt-in.
    #   second_gate  the first brief per browser is anonymous; a second one asks
    #                for a sign-in. A soft prompt, never a dead end: the squad
    #                stays on screen and the 403 carries `sign_in_required`.
    #   login_first  every brief needs a session.
    #
    # The tradeoff is recorded in docs/layer5 §8 (2026-08-06). Read it before
    # moving off `open`: the free brief with no login is the top of the entire
    # funnel, and the gate trades reach for addresses.
    #
    # An unrecognised value REFUSES TO BOOT (api/main._check_access_config).
    # Neither default is safe to guess: falling back to `open` silently ignores a
    # deliberate gate, and falling back to `login_first` silently walls the free
    # product. A typo here should be a deploy-log line, not a discovery.
    free_brief_access: str = "open"                     # FREE_BRIEF_ACCESS

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

    # --- accounts + auth (2026-08-04) --------------------------------------
    # Google OAuth. Empty client id => the Google buttons/routes are OFF (404),
    # rather than half-present and failing at the redirect. Magic link still works.
    google_client_id: str = ""                          # GOOGLE_CLIENT_ID
    google_client_secret: str = ""                      # GOOGLE_CLIENT_SECRET
    # Where Google sends the user back. Must match the console entry EXACTLY,
    # including scheme and trailing path — Google compares it as a string.
    google_redirect_uri: str = "http://127.0.0.1:8000/api/auth/google/callback"

    # Terms version presented at signup. BUMP THIS when the terms change: an
    # account whose stored version differs is prompted to re-accept before paid
    # content is served (`accounts.needs_terms_acceptance`). A date-like string
    # rather than a number so the value is self-documenting in the DB.
    #
    # Must stay equal to the VERSION const on web/app/terms/page.tsx AND on
    # web/app/privacy/page.tsx — all three move together or the site shows a
    # version nobody was asked to accept.
    #
    # 2026-08-04 -> 2026-08-07: /terms stopped being a TODO placeholder and
    # became real terms. The bump is the point, not a side effect — an account
    # holding "2026-08-04" accepted a page that said "TODO, write the terms", and
    # carrying that forward as agreement to the real text would be a consent
    # record for words nobody ever saw. /refunds carries its own display-only
    # version and is deliberately NOT this value: it is not part of the
    # acceptance gate, so editing it must not re-prompt every account.
    terms_version: str = "2026-08-07"                   # TERMS_VERSION

    # Session lifetime. 30 days: long enough that a weekly product does not ask
    # for a link every visit, short enough that an abandoned laptop expires.
    session_ttl_days: int = 30                          # SESSION_TTL_DAYS
    # Magic link lifetime, in minutes. Short by design — the link IS the credential.
    magic_link_ttl_minutes: int = 15                    # MAGIC_LINK_TTL_MINUTES

    # Cookie flags. Secure defaults ON: the only environment where it must be off
    # is plain-http local dev, and defaulting the other way means one forgotten
    # env var ships session cookies in the clear.
    session_cookie_secure: bool = True                  # SESSION_COOKIE_SECURE
    # 'lax' is right when the API and the web app share a registrable domain
    # (api.example.com + example.com), which is the launch topology. 'none'
    # (which REQUIRES Secure) is the escape hatch for a genuinely cross-site
    # deployment. Never 'strict': it drops the cookie on the magic-link click,
    # because that arrives as a cross-site top-level navigation from an email.
    session_cookie_samesite: str = "lax"                # SESSION_COOKIE_SAMESITE
    # Set to the parent domain (".example.com") to share the cookie between the
    # web app and the API. Empty = host-only, which is correct for local dev.
    session_cookie_domain: str = ""                     # SESSION_COOKIE_DOMAIN

    # Magic-link requests per IP per window. HARD limit: this endpoint sends mail
    # to an address the caller names, so it is both a spam cannon and a bill.
    # Durable (DbRateLimiter), because an in-process counter resets on every
    # Render wake and this one has to hold across them.
    auth_link_limit: int = 5                            # AUTH_LINK_LIMIT
    auth_link_window_s: int = 3600                      # AUTH_LINK_WINDOW_S
    # Per-ADDRESS ceiling, separate from the per-IP one. Without it, a botnet
    # mails one victim from a thousand IPs and every request is under the per-IP cap.
    auth_link_per_email_limit: int = 5                  # AUTH_LINK_PER_EMAIL_LIMIT
    # Auth verifications/callbacks per IP per window — a token-guessing ceiling.
    auth_verify_limit: int = 30                         # AUTH_VERIFY_LIMIT
    auth_verify_window_s: int = 900                     # AUTH_VERIFY_WINDOW_S


settings = ApiSettings()
