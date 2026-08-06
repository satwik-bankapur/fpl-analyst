"""FastAPI app: CORS, error mapping, and the single-process scheduler (spec §5).

SINGLE-REPLICA INVARIANT (hard requirement): APScheduler runs in-process here. A
second replica = duplicate syncs and duplicate emails to paying customers. Railway
must pin numReplicas=1 (see railway.toml); if scale ever needs replicas, extract
the scheduler into its own service first. Startup is gated on RUN_SCHEDULER so tests
and local demos never spawn it.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import tokens
from .config import settings
from .ratelimit import RateLimited
from .routes import router
from .service import FREE_BRIEF_ACCESS_MODES, BriefError

log = logging.getLogger("api")

# graph PipelineError codes -> HTTP status (spec §1.1). invalid_draft_id is ours.
_ERROR_STATUS = {
    "invalid_team_id": 404,
    "invalid_draft_id": 404,
    "no_picks_yet": 409,
    "fpl_api_down": 503,
    "empty_ep_table": 503,
}


def _check_stage_b_config() -> None:
    """Fail CLOSED at startup on a Stage B misconfiguration.

    A weak or missing `BRIEF_TOKEN_SECRET` is not a lint issue: HS256 with a
    guessable key means anyone can forge a full-view token for any team, which is
    the paid product given away. The right time to discover that is at boot, in a
    deploy log, not when a customer's link silently stops meaning anything.

    Only checked when a Stage B feature is actually ON, so Stage A tooling, the CLI
    and the test suite still start with everything empty.
    """
    if settings.paywall_enabled or settings.send_enabled:
        tokens.assert_secret_usable()
    if settings.paywall_enabled and not settings.paddle_webhook_secret:
        # Not fatal: the paywall works standing alone (it just locks sections), and
        # refusing to boot would take the whole site down over a billing setting.
        # But nothing can ever unlock, so say so loudly.
        log.warning(
            "PAYWALL_ENABLED is true but PADDLE_WEBHOOK_SECRET is empty — the "
            "webhook rejects everything, so no subscription can ever be granted. "
            "Paid sections will stay locked for every customer.")
    if settings.send_enabled and not settings.resend_api_key:
        log.warning(
            "SEND_ENABLED is true but RESEND_API_KEY is empty — the batch will "
            "run, claim send_log rows, and deliver nothing.")


def _check_access_config() -> None:
    """Refuse to boot on an unrecognised FREE_BRIEF_ACCESS.

    There is no safe default to fall back to. Guessing `open` silently ignores a
    gate someone deliberately configured; guessing `login_first` silently walls
    the free product, which is the top of the whole funnel. Both failures are
    invisible until someone notices the conversion numbers moved. A typo belongs
    in a deploy log.
    """
    if settings.free_brief_access not in FREE_BRIEF_ACCESS_MODES:
        raise RuntimeError(
            f"FREE_BRIEF_ACCESS={settings.free_brief_access!r} is not one of "
            f"{sorted(FREE_BRIEF_ACCESS_MODES)}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_access_config()
    _check_stage_b_config()
    scheduler = None
    if settings.run_scheduler:
        from fpl.scheduler import build_scheduler  # in-process, single replica only
        scheduler = build_scheduler()
        scheduler.start()
        log.info("in-process sync scheduler started (single replica)")
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


# Interactive docs are OFF unless DOCS_ENABLED=true. The generated schema lists
# every /api/admin/* path and its exact request body — a free map of the gated
# surface for anyone probing the public one. Nothing in the product reads it.
app = FastAPI(
    title="FPL Analyst API",
    lifespan=lifespan,
    docs_url="/docs" if settings.docs_enabled else None,
    redoc_url="/redoc" if settings.docs_enabled else None,
    openapi_url="/openapi.json" if settings.docs_enabled else None,
)

# `allow_credentials=True` is what lets the session cookie cross from the Next.js
# origin to this one (accounts build, 2026-08-04). It is safe ONLY because
# `allow_origins` is a single exact origin from config and never `*` — the two
# settings are a pair, and CORS refuses the wildcard-plus-credentials combination
# outright rather than letting anyone's page read authenticated responses.
# If `FRONTEND_ORIGIN` ever becomes a list or a regex, re-derive this decision.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.exception_handler(BriefError)
async def _brief_error(_: Request, exc: BriefError):
    status = _ERROR_STATUS.get(exc.code, 500)
    body = {"error": exc.code}
    if exc.code == "no_picks_yet":
        body["hint"] = "preseason_draft"
    return JSONResponse(status_code=status, content=body)


@app.exception_handler(RateLimited)
async def _rate_limited(_: Request, exc: RateLimited):
    return JSONResponse(
        status_code=429,
        content={"error": "rate_limited"},
        headers={"Retry-After": str(exc.retry_after_s)},
    )


app.include_router(router)
